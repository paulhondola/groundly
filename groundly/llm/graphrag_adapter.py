"""Translates Groundly's own provider config into graphrag's config primitives — the
one place doing so (.claude/rules/architecture.md: LLM clients constructed only in
llm/, same interpretation already implied by embeddings.py/rerank.py). graphrag's
LiteLLM-based client speaks the same OpenAI-compatible base_url+model+key shape
Groundly already assumes everywhere else.
"""

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path
from urllib.parse import urlparse

from graphrag.config.defaults import graphrag_config_defaults

# litellm's env defaults (price map, log level) are set in groundly/__init__.py — here
# was too late: graphrag_llm.embedding.embedding below pulls litellm in at *its* module
# load, and callers like ingestion/graph.py import graphrag before importing this module.
from graphrag_llm.config import MetricsConfig, ModelConfig
from graphrag_llm.config.rate_limit_config import RateLimitConfig
from graphrag_llm.config.retry_config import RetryConfig
from graphrag_llm.embedding.embedding import LLMEmbedding
from graphrag_llm.embedding.embedding_factory import register_embedding
from graphrag_llm.metrics.memory_metrics_store import MemoryMetricsStore
from graphrag_llm.metrics.metrics_store_factory import register_metrics_store
from graphrag_llm.retry.exceptions_to_skip import _default_exceptions_to_skip
from graphrag_vectors import VectorStoreConfig

from groundly.core.manifest import EMBEDDING_DIM
from groundly.llm.chat import _LOCAL_PLACEHOLDER_KEY
from groundly.llm.config import ProviderConfig, load_settings, require_provider

BGE_M3_EMBEDDING_TYPE = "bge_m3"
GROUNDLY_METRICS_STORE_TYPE = "groundly"

# The keys graphrag looks completion_models/embedding_models up by. **Build and query
# MUST agree on these**, and until they lived here they did not have to: both
# ingestion/graph.py and retrieval/graph.py declared their own copies, so a rename in
# one silently broke the lookup in the other. Now the agreement is structural — there is
# one definition and both import it.
COMPLETION_MODEL_ID = "default_completion_model"
REPORT_COMPLETION_MODEL_ID = "report_completion_model"
EMBEDDING_MODEL_ID = "default_embedding_model"

_BUNDLED_PROMPT = ("groundly", "prompts/extract_graph.txt")

# graphrag's extractor formats the prompt with exactly these two keys
# (graph_extractor._process_document) — nothing else is interpolated.
_REQUIRED_PLACEHOLDERS = ("{entity_types}", "{input_text}")

# ...and these are *not*, despite reading like they would be. graphrag 3.1.0 writes the
# delimiters into the prompt literally and parses with hardcoded TUPLE_DELIMITER /
# RECORD_DELIMITER / COMPLETION_DELIMITER constants. A prompt carrying them raises
# KeyError inside graph_extractor's per-chunk `except Exception`, which is swallowed and
# logged — i.e. every chunk fails silently. Rejecting them up front is cheaper than
# discovering that through the failure gate hours later.
_FORBIDDEN_PLACEHOLDERS = ("{tuple_delimiter}", "{record_delimiter}", "{completion_delimiter}")


class ExtractionPromptError(Exception):
    """A configured `graph.extraction_prompt` that cannot be used — missing, unreadable,
    or malformed. Named cause, raised before any LLM call.

    Lives here rather than in ingestion/ because llm/ is a foundation *below* ingestion
    (.claude/rules/architecture.md); the dependency may not point the other way.
    """


def _bundled_prompt_text() -> str:
    return files(_BUNDLED_PROMPT[0]).joinpath(_BUNDLED_PROMPT[1]).read_text(encoding="utf-8")


def _validate_prompt(text: str, source: str) -> None:
    missing = [p for p in _REQUIRED_PLACEHOLDERS if p not in text]
    if missing:
        raise ExtractionPromptError(
            f"{source} is missing the placeholder(s) {', '.join(missing)} — graphrag "
            "substitutes the entity types and the chunk text there, so a prompt without "
            "them would send every chunk the same literal text"
        )
    present = [p for p in _FORBIDDEN_PLACEHOLDERS if p in text]
    if present:
        raise ExtractionPromptError(
            f"{source} contains {', '.join(present)}, which graphrag does not substitute "
            "— it writes the delimiters into the prompt literally and parses with fixed "
            "ones. Leaving these in makes every chunk fail silently; write the "
            "delimiters out as <|>, ## and <|COMPLETE|> instead"
        )


@contextmanager
def resolve_extraction_prompt() -> Iterator[tuple[Path, str]]:
    """Yield `(path, text)` for the entity-extraction prompt the build will send.

    A path, not just text: `ExtractGraphConfig.prompt` is a *filesystem path* and
    `resolved_prompts()` does `Path(self.prompt).read_text()`, so the file has to exist
    for as long as the build runs. `as_file()` is what makes that true for a zipped
    install as well as an unzipped one, which is why this is a context manager.

    Validated here so a bad override is a named error before any LLM call, rather than a
    graphrag internal surfacing hours in.
    """
    configured = load_settings().graph.extraction_prompt
    if configured:
        path = Path(configured).expanduser()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ExtractionPromptError(
                f"graph.extraction_prompt points at {path}, which could not be read "
                f"({exc.strerror or exc}). Fix the path in your config.toml, or unset it "
                "to use the bundled course-tuned prompt"
            ) from exc
        _validate_prompt(text, f"the extraction prompt at {path}")
        yield path, text
        return

    with as_file(files(_BUNDLED_PROMPT[0]).joinpath(_BUNDLED_PROMPT[1])) as path:
        text = path.read_text(encoding="utf-8")
        _validate_prompt(text, "the bundled extraction prompt")
        yield path, text


def extraction_entity_types() -> list[str]:
    """`graph.entity_types`, split and stripped. Stored comma-separated (see
    core/config.GraphSettings) because the config writer emits scalars only."""
    return [t.strip() for t in load_settings().graph.entity_types.split(",") if t.strip()]


def extraction_fingerprint(prompt_text: str, entity_types: list[str], gleanings: int = 0) -> str:
    """sha256 over exactly what the build sends: the prompt text, the entity-type list as
    graphrag joins it, and the number of gleaning rounds. Not sorted — reordering the
    types genuinely changes the prompt the model sees, so it counts as a change.

    `gleanings` belongs here because it changes what is *sent*, not merely how the result
    is post-processed: a gleaning round is a second extraction call per chunk carrying
    graphrag's CONTINUE_PROMPT. Two apd builds differing in **this field alone** produced
    3,704 and 6,184 entities at 1,175 and 2,352 extraction calls, and nothing recorded
    anywhere said they were different builds. Folding it into the fingerprint makes
    `graph_is_stale` say so and offer a rebuild — the whole point, at no manifest cost.

    Default 0 so a caller that predates the parameter fingerprints identically to before;
    every in-tree caller passes it explicitly."""
    return hashlib.sha256(
        f"{prompt_text}\n{','.join(entity_types)}\ngleanings={gleanings}".encode()
    ).hexdigest()


# Set once by allow_nonstandard_service_tier(); see there for why this is a flag rather
# than a check against the field's current annotation.
_service_tier_widened = False


def completion_model_config(
    track_usage: bool = False, call_class: str = "extraction"
) -> ModelConfig:
    """Build graphrag's ModelConfig from one of Groundly's provider sections
    (`call_class`, default `extraction` — `graph.report_call_class` points community
    reports at another one). Fails fast (via require_provider) — a *configured* provider
    is always required, but not necessarily a real API key: graphrag's own ModelConfig
    validator rejects an empty api_key outright (unlike Groundly's own llm/chat.py, which
    just omits the Authorization header when `cfg.api_key` is empty), so a local/keyless
    provider (LM Studio, Ollama) needs a truthy placeholder here to pass that validation —
    the placeholder is never checked by a local server, same as the empty-header path
    already works for every other call class.

    `track_usage` swaps graphrag_llm's metrics store for one this process can read back
    (see `metered_usage`), which is how the *batch build* learns what it actually spent.
    The query path leaves it off and keeps the stock store.

    `reasoning_effort`, when the provider sets it, goes into `call_args["extra_body"]` —
    measured: `call_args={"reasoning_effort": ...}` makes litellm raise
    UnsupportedParamsError on every call (its `drop_params` is False, so it never
    degrades), while nesting the same value under `extra_body` reaches the provider
    (93 -> 2 completion tokens on a local reasoning model). Omitted entirely when unset,
    so `call_args` keeps its `{}` default and today's behavior is unchanged."""
    cfg = require_provider(call_class)
    extra = (
        {"call_args": {"extra_body": {"reasoning_effort": cfg.reasoning_effort}}}
        if cfg.reasoning_effort
        else {}
    )
    return ModelConfig(
        model_provider="openai",
        model=cfg.model,
        api_base=cfg.base_url,
        api_key=cfg.api_key or _LOCAL_PLACEHOLDER_KEY,
        retry=_retry_config(),
        rate_limit=_rate_limit_config(cfg),
        # `MetricsConfig()` rather than None when tracking is off: None switches metrics
        # off entirely and takes the indexing log's per-model summary with it.
        metrics=MetricsConfig(store=GROUNDLY_METRICS_STORE_TYPE)
        if track_usage
        else MetricsConfig(),
        **extra,
    )


def bge_m3_embedding_models() -> dict[str, ModelConfig]:
    """The `embedding_models` entry both graph paths register. One definition, because
    the *key* is what graphrag resolves the embedder by and the build and the query have
    to name the same one (see EMBEDDING_MODEL_ID)."""
    return {
        EMBEDDING_MODEL_ID: ModelConfig(
            type=BGE_M3_EMBEDDING_TYPE,
            model_provider=BGE_M3_EMBEDDING_TYPE,
            model="bge-m3",
        )
    }


def graph_vector_store(graph_dir: Path) -> VectorStoreConfig:
    """Where a subject's graph keeps its LanceDB entity-description vectors. The build
    writes it and the query reads it, so the path and the dimension are one definition
    rather than two that happen to match."""
    return VectorStoreConfig(db_uri=str(graph_dir / "lancedb"), vector_size=EMBEDDING_DIM)


class ReadableMetricsStore(MemoryMetricsStore):
    """graphrag_llm's own in-memory metrics store, plus a handle on every instance.

    graphrag aggregates real per-model usage but only ever *writes* it from
    `MemoryMetricsStore._on_exit_`, registered with `atexit` — so the log line and the
    file writer alike land after the interpreter is done, long past any point a build
    could read them. `get_metrics()` has the same numbers live; all that was missing was
    a reference to the store holding them. Keeping the log writer on means the indexing
    log still gets its end-of-run summary, unchanged.

    Keyed by `id` (graphrag's `model_provider/model`) rather than held as a single
    `latest` pointer, because graphrag_llm caches these as singletons keyed on hashed
    init args *including* that `id` — see `graphrag_common/factory/factory.py`'s
    `cache_key` and `graphrag_llm/metrics/metrics_store_factory.py` passing `"id": id`
    into those args. So `graph.report_call_class` pointing community reports at a second
    model produces a SECOND store here, not a second write into the first, and
    `metered_usage` sums over every instance rather than trusting there is only ever one.
    """

    instances: "dict[str, ReadableMetricsStore]" = {}

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        type(self).instances[self.id] = self


def register_groundly_metrics_store() -> None:
    """Register `ReadableMetricsStore` under the `groundly` store name. Idempotent:
    the factory's register() is a plain dict assignment (graphrag_common/factory.py),
    mirroring register_bge_m3_embedding."""
    register_metrics_store(GROUNDLY_METRICS_STORE_TYPE, ReadableMetricsStore, "singleton")


def _retry_config() -> RetryConfig:
    """Always on. graphrag fires extraction concurrently across the whole corpus and
    swallows a 429 per text unit exactly like any other failure, so without a retry a
    rate-limited provider silently drops chunks — observed as 283 failures out of 304
    against Groq. Jitter matters as much as the backoff: without it every concurrent
    worker retries in lockstep and re-creates the burst that caused the 429.

    base_delay must be strictly > 1.0 for exponential backoff (graphrag validates it);
    2.0 gives 2/4/8/16/32s, capped at max_delay.

    `exceptions_to_skip` drops BadRequestError from graphrag_llm's default never-retry
    list. A local runtime reports *capacity* exhaustion as a 400 — llama.cpp/LM Studio
    answer "Context size has been exceeded" when concurrent slots overrun the shared KV
    cache — and litellm maps that to the same BadRequestError as a genuinely malformed
    request, so graphrag retried it zero times and dropped the community report. Measured
    2026-08-01: 8 of 11 report calls died in waves of exactly n_slots, and the final wave
    of 3 succeeded unaided once the other slots had drained — which is precisely what a
    backed-off retry recreates. `concurrent_requests()` below is the actual fix; this is
    the backstop for the runtimes it cannot detect.

    The cost when the 400 *is* structural: 2/4/8/16/32s before the failure surfaces.
    Bounded and rare — ingestion/graph.py's probe screens that case up front, and it calls
    llm/chat.py's complete() (litellm directly, not graphrag's retrier), so probe latency
    is unchanged. Importing graphrag_llm's private default is deliberate: a pin bump that
    renames it raises ImportError here rather than silently reinstating the old behavior."""
    return RetryConfig(
        max_retries=5,
        base_delay=2.0,
        max_delay=60.0,
        jitter=True,
        exceptions_to_skip=[e for e in _default_exceptions_to_skip if e != "BadRequestError"],
    )


# graphrag has ONE global concurrency setting covering every stage (extract_graph,
# summarize_descriptions, create_community_reports all pass `num_threads=
# config.concurrent_requests`), and leaves it at 25.
_LOCAL_CONCURRENT_REQUESTS = 1

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


def _is_loopback(cfg: ProviderConfig) -> bool:
    host = urlparse(cfg.base_url).hostname
    return bool(host) and (host in _LOOPBACK_HOSTS or host.endswith(".localhost"))


def concurrent_requests(*cfgs: ProviderConfig) -> int:
    """How many calls a graph build may keep in flight. 1 against a local runtime,
    graphrag's own default against everything else.

    prompt_budgets() below sizes every stage to fit `graph.context_window` *once*. A
    llama.cpp-family server does not work that way: LM Studio loads with `n_slots = 4,
    kv_unified = 'true'`, i.e. four concurrent requests drawing from ONE cache of that
    size. Measured 2026-08-01 on gemma-4-12b-qat at 8192: community-report prompts are
    ~2,300 tokens each, 4 in flight need ~9,200, and llama.cpp walks its batch size down
    1024 -> 1 before answering `decode: Context size has been exceeded`. Extraction
    survived the same build only because its prompts are 450-750 tokens.

    So the budget is per *request* while the runtime's limit is per *cache*, and Groundly
    cannot see the divisor — `n_slots` is a load-time setting no OpenAI-compatible
    endpoint reports. Serializing is the honest answer, and it is close to free: measured
    150 tok/s prompt-eval with one slot busy against 30-60 tok/s with four contending,
    because they share the same GPU either way.

    Loopback is the signal because a shared KV cache is what "the model runs on this
    machine" means. It is a heuristic with one known gap — a local runtime reached over
    the LAN looks remote and still needs its slots reduced by hand (docs/guides/
    graphrag-provider.md says so).

    Variadic and pessimistic ("any local wins") because of that single global setting:
    with `graph.report_call_class` pointing reports at a cloud model, a local extraction
    provider still binds the whole build."""
    return (
        _LOCAL_CONCURRENT_REQUESTS
        if any(_is_loopback(cfg) for cfg in cfgs)
        else graphrag_config_defaults.concurrent_requests
    )


def _rate_limit_config(cfg: ProviderConfig) -> RateLimitConfig | None:
    """Only when the provider's limits have been declared in config.toml. There is no
    portable way to discover them, and guessing would throttle a local runtime that
    has no limits at all — so unset means unthrottled, the previous behavior."""
    if cfg.requests_per_minute is None and cfg.tokens_per_minute is None:
        return None
    return RateLimitConfig(
        period_in_seconds=60,
        requests_per_period=cfg.requests_per_minute,
        tokens_per_period=cfg.tokens_per_minute,
    )


@dataclass(frozen=True)
class PromptBudgets:
    """graphrag's per-stage prompt sizing, scaled to the extraction model's context."""

    max_gleanings: int
    summarize_max_input_tokens: int
    summarize_max_length: int
    community_max_input_length: int
    community_max_length: int


def prompt_budgets(context_window: int, gleanings: int = 0) -> PromptBudgets:
    """Scale graphrag's stage budgets to the model actually configured.

    graphrag's defaults assume a large-context cloud model: community reports alone
    ask for 8000 tokens in + 2000 out, and entity extraction sends a ~1620-token
    few-shot preamble plus the chunk, then replays the whole conversation for a
    gleaning round. On a 4096-token local model every one of those 400s with
    "Context size has been exceeded", and graphrag swallows the failure per text
    unit — an empty graph reported as a successful build.

    Each budget is `min(graphrag's default, a share of the window)`, so this only
    ever scales *down*: a large context_window reproduces stock graphrag behavior.

    These are *per-request* budgets, and that is only half the constraint. A llama.cpp
    -family runtime serves several requests from one shared KV cache, so what has to fit
    is `in-flight calls x prompt`, not one prompt — see concurrent_requests() above, which
    is what keeps the divisor at 1 locally so these numbers mean what they say.
    """
    return PromptBudgets(
        # `gleanings` is the student's choice (`graph.gleanings`); the window only ever
        # *clamps* it. A gleaning round re-sends prompt + chunk + the model's whole first
        # answer, so it roughly doubles peak context — below 16384 that does not fit
        # beside the stage budgets carved out above, and asking for it anyway would fail
        # every call rather than extract more.
        #
        # This used to BE the choice (`1 if context_window >= 16384 else 0`), which made a
        # capacity setting silently control cost: raising apd's window from 12288 to 16384
        # doubled its extraction calls and its bill, with nothing in the manifest recording
        # that the *procedure* had changed at all. See core/config.GraphSettings.gleanings
        # for the controlled numbers — and note the isolated-entity rate it does NOT move.
        max_gleanings=gleanings if context_window >= 16384 else 0,
        summarize_max_input_tokens=min(4000, context_window // 2),
        summarize_max_length=min(500, context_window // 4),
        community_max_input_length=min(8000, context_window // 2),
        community_max_length=min(2000, context_window // 4),
    )


class Bgem3GraphEmbedding(LLMEmbedding):
    """graphrag's entity-description embedding store, delegated to the already-loaded
    BgeM3Embedder — zero marginal cost, zero new provider config (graph build stays
    cheap beyond the one extraction cost). Dense vectors only: graphrag's embedding
    store only needs similarity, not our sparse channel.

    `embedder` mirrors VectorRetriever's pattern (retrieval/vector.py): None in
    production (lazily resolves to the process-wide `shared_embedder()` singleton on
    first use, same resident model VectorRetriever uses), or a stub injected by tests.
    """

    def __init__(
        self,
        *,
        model_id: str = "",
        model_config: ModelConfig | None = None,
        tokenizer=None,
        metrics_store=None,
        embedder=None,
        **kwargs,
    ) -> None:
        self._model_id = model_id
        self._tokenizer = tokenizer
        self._metrics_store = metrics_store
        self._embedder = embedder

    @property
    def embedder(self):
        if self._embedder is None:
            from groundly.llm.embeddings import shared_embedder

            self._embedder = shared_embedder()
        return self._embedder

    def embedding(self, /, **kwargs):
        from graphrag_llm.types import LLMEmbedding as EmbeddingItem
        from graphrag_llm.types import LLMEmbeddingResponse, LLMEmbeddingUsage

        texts = kwargs["input"]
        dense, _sparse = self.embedder.encode(texts)
        data = [
            EmbeddingItem(object="embedding", embedding=vec, index=i) for i, vec in enumerate(dense)
        ]
        return LLMEmbeddingResponse(
            object="list",
            data=data,
            model=self._model_id,
            usage=LLMEmbeddingUsage(prompt_tokens=0, total_tokens=0),
        )

    async def embedding_async(self, /, **kwargs):
        return self.embedding(**kwargs)

    @property
    def metrics_store(self):
        return self._metrics_store

    @property
    def tokenizer(self):
        return self._tokenizer


def allow_nonstandard_service_tier() -> None:
    """Widen `graphrag_llm.LLMCompletionResponse.service_tier` from OpenAI's literal
    set to any string.

    graphrag_llm builds its response as `LLMCompletionResponse(**response.model_dump())`
    (lite_llm_completion.py) and types `service_tier` as
    `Literal['auto','default','flex','scale','priority']` — OpenAI's exact enum. Groq
    returns `'on_demand'`, so pydantic rejects **every** response: the HTTP calls
    succeed, tokens are spent, and the results are discarded at parse time. Observed
    as 258 requests, 258 failures, `No entities detected during extraction`.

    Not a Groq special case — the field is provider-reported metadata that graphrag
    never reads, and any OpenAI-compatible provider may put its own value there
    (.claude/rules/architecture.md: never hardcode a provider). The construction site
    is a closure inside a factory function, so there is no subclass or
    `register_completion` seam to override; widening the field is the available fix.

    Idempotent, and safe for OpenAI itself: `'default'` and `None` still validate.

    The guard is a module flag, not a comparison against the annotation: `str | None`
    builds a fresh `types.UnionType` on every evaluation, so `field.annotation is not
    (str | None)` is *always* true and would re-run `model_rebuild(force=True)` on every
    call — including once per graph query, concurrently, on a shared third-party model
    class. pydantic makes no thread-safety promise for that.
    """
    global _service_tier_widened

    if _service_tier_widened:
        return

    from graphrag_llm.types.types import LLMCompletionResponse

    LLMCompletionResponse.model_fields["service_tier"].annotation = str | None
    LLMCompletionResponse.model_rebuild(force=True)
    _service_tier_widened = True


def register_bge_m3_embedding() -> None:
    """Register Bgem3GraphEmbedding under the `bge_m3` strategy name. Idempotent:
    the factory's register() is a plain dict assignment (graphrag_common/factory.py),
    so calling this more than once just re-assigns the same entry."""
    register_embedding(BGE_M3_EMBEDDING_TYPE, Bgem3GraphEmbedding)
