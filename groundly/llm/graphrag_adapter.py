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

from groundly.llm.chat import _LOCAL_PLACEHOLDER_KEY
from groundly.llm.config import ProviderConfig, load_provider, load_settings, require_provider

BGE_M3_EMBEDDING_TYPE = "bge_m3"
GROUNDLY_METRICS_STORE_TYPE = "groundly"

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


def extraction_fingerprint(prompt_text: str, entity_types: list[str]) -> str:
    """sha256 over exactly what the build sends: the prompt text and the entity-type
    list as graphrag joins it. Not sorted — reordering the types genuinely changes the
    prompt the model sees, so it counts as a change."""
    return hashlib.sha256(f"{prompt_text}\n{','.join(entity_types)}".encode()).hexdigest()


def _preamble_tokens() -> int:
    """The extraction preamble, sent with every single chunk. Measured off the prompt
    that will actually be used, so it tracks a custom prompt instead of going stale.

    Falls back to the bundled prompt when an override is unreadable: this feeds
    `estimate_cost`, which is an estimate and must degrade rather than fail. The named
    failure is `build_graph`'s job, and it still fires before any LLM call.
    """
    try:
        with resolve_extraction_prompt() as (_path, text):
            return len(text) // 4
    except ExtractionPromptError:
        return len(_bundled_prompt_text()) // 4


# Set once by allow_nonstandard_service_tier(); see there for why this is a flag rather
# than a check against the field's current annotation.
_service_tier_widened = False


def completion_model_config(track_usage: bool = False) -> ModelConfig:
    """Build graphrag's ModelConfig from Groundly's `extraction` provider. Fails fast
    (via require_provider) — a *configured* provider is always required, but not
    necessarily a real API key: graphrag's own ModelConfig validator rejects an empty
    api_key outright (unlike Groundly's own llm/chat.py, which just omits the
    Authorization header when `cfg.api_key` is empty), so a local/keyless provider
    (LM Studio, Ollama) needs a truthy placeholder here to pass that validation — the
    placeholder is never checked by a local server, same as the empty-header path
    already works for every other call class.

    `track_usage` swaps graphrag_llm's metrics store for one this process can read back
    (see `metered_usage`), which is how the *batch build* learns what it actually spent.
    The query path leaves it off and keeps the stock store."""
    cfg = require_provider("extraction")
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
    )


class _ReadableMetricsStore(MemoryMetricsStore):
    """graphrag_llm's own in-memory metrics store, plus a handle on the instance.

    graphrag aggregates real per-model usage but only ever *writes* it from
    `MemoryMetricsStore._on_exit_`, registered with `atexit` — so the log line and the
    file writer alike land after the interpreter is done, long past any point a build
    could read them. `get_metrics()` has the same numbers live; all that was missing was
    a reference to the store holding them. Keeping the log writer on means the indexing
    log still gets its end-of-run summary, unchanged.
    """

    latest: "_ReadableMetricsStore | None" = None

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        type(self).latest = self


def register_groundly_metrics_store() -> None:
    """Register `_ReadableMetricsStore` under the `groundly` store name. Idempotent:
    the factory's register() is a plain dict assignment (graphrag_common/factory.py),
    mirroring register_bge_m3_embedding."""
    register_metrics_store(GROUNDLY_METRICS_STORE_TYPE, _ReadableMetricsStore, "singleton")


@dataclass(frozen=True)
class MeteredUsage:
    """What a graph build actually spent, from graphrag's own aggregates."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float | None


def metered_usage() -> MeteredUsage | None:
    """The usage graphrag accumulated since `reset_metered_usage()`, priced.

    **Cache hits are counted in the token totals but were never paid for** — a rebuild
    against a warm cache reported 420,965 prompt tokens at `cache_hit_rate: 1.0`, none of
    which cost anything. That is the normal path here, not an edge case: decision 21
    deliberately preserves `cache/` across a failed rebuild so the retry keeps the
    responses already bought. Tokens stay as metered (they were genuinely processed); the
    *cost* is scaled to the responses that actually reached the provider.

    Returns None on anything unexpected. This is a number printed after a successful
    build — it must never be the reason one fails.
    """
    store = _ReadableMetricsStore.latest
    if store is None:
        return None
    try:
        metrics = store.get_metrics()
        prompt_tokens = int(metrics["prompt_tokens"])
        completion_tokens = int(metrics["completion_tokens"])
        responses = int(metrics["responses_with_tokens"])
        cached = int(metrics.get("cached_responses", 0))
        prices = extraction_prices()
    except Exception:  # noqa: BLE001 — see the docstring: never fail a finished build
        return None

    # A store that metered nothing has nothing to report — and saying "0 tokens, $0.00"
    # would read as a fact rather than as an absence.
    if prompt_tokens + completion_tokens == 0:
        return None

    cost = None
    if prices is not None and responses > 0:
        billed = max(0, responses - cached) / responses
        cost = billed * (
            prompt_tokens * prices.input_per_token + completion_tokens * prices.output_per_token
        )
    return MeteredUsage(prompt_tokens, completion_tokens, prompt_tokens + completion_tokens, cost)


def reset_metered_usage() -> None:
    """Zero any store a previous build left behind, so `metered_usage()` can only ever
    return this build's numbers. graphrag registers stores as singletons, so a repeat
    build in the same process reuses the first one's store and would otherwise keep
    accumulating into its totals. The handle is deliberately *not* dropped — that reused
    store is the one the repeat build writes into.

    That reuse is keyed on *hashed init args* (graphrag_common/factory.create), so it
    only holds while the ModelConfig is unchanged. Change `extraction.model` or
    `base_url` between two in-process builds and graphrag constructs a second store while
    `latest` still points at the first, at which point `metered_usage()` reports None (or,
    if the config is then changed back, an earlier build's totals). **One build per
    process** is therefore the real constraint, and it is what `groundly index` does —
    anything that grows a second in-process build must re-derive the handle rather than
    trust this reset."""
    if _ReadableMetricsStore.latest is not None:
        _ReadableMetricsStore.latest.clear_metrics()


def _retry_config() -> RetryConfig:
    """Always on. graphrag fires extraction concurrently across the whole corpus and
    swallows a 429 per text unit exactly like any other failure, so without a retry a
    rate-limited provider silently drops chunks — observed as 283 failures out of 304
    against Groq. Jitter matters as much as the backoff: without it every concurrent
    worker retries in lockstep and re-creates the burst that caused the 429.

    base_delay must be strictly > 1.0 for exponential backoff (graphrag validates it);
    2.0 gives 2/4/8/16/32s, capped at max_delay."""
    return RetryConfig(max_retries=5, base_delay=2.0, max_delay=60.0, jitter=True)


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


def prompt_budgets(context_window: int) -> PromptBudgets:
    """Scale graphrag's stage budgets to the model actually configured.

    graphrag's defaults assume a large-context cloud model: community reports alone
    ask for 8000 tokens in + 2000 out, and entity extraction sends a ~1620-token
    few-shot preamble plus the chunk, then replays the whole conversation for a
    gleaning round. On a 4096-token local model every one of those 400s with
    "Context size has been exceeded", and graphrag swallows the failure per text
    unit — an empty graph reported as a successful build.

    Each budget is `min(graphrag's default, a share of the window)`, so this only
    ever scales *down*: a large context_window reproduces stock graphrag behavior.
    """
    return PromptBudgets(
        # The gleaning round re-sends prompt + chunk + the model's whole first answer.
        # On a chunk capped at CHUNK_MAX_TOKENS (512) one pass has already seen
        # everything, so it buys little and roughly doubles peak context.
        max_gleanings=1 if context_window >= 16384 else 0,
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


@dataclass(frozen=True)
class ModelPrices:
    """Per-token prices for one model, plus where they came from — the source string is
    printed at the spend gate, because a price the student can't attribute is a price
    they can't sanity-check."""

    input_per_token: float
    output_per_token: float
    source: str


def _litellm_prices(model: str) -> ModelPrices | None:
    """litellm's bundled price map, looked up by the bare model name from config.toml.

    litellm keys OpenAI models bare (`gpt-4o-mini`) but everything else with its
    provider (`groq/llama-3.3-70b-versatile`, `mistral/mistral-large-latest`) — 517
    bare against 2199 prefixed. Groundly only ever knows the bare name, because the
    provider is expressed as a `base_url`, so a plain `.get()` silently misses every
    non-OpenAI model. Fall back to a suffix match, which stays provider-agnostic
    (.claude/rules/architecture.md: never hardcode a provider).

    Only a *unique* suffix match counts: an ambiguous bare name must return None
    rather than quietly bill against some unrelated provider's price.

    Both prices must be present. A half-priced entry would produce a range whose upper
    bound silently omits output — the exact failure this function's caller exists to fix.
    """
    import litellm

    key, entry = model, litellm.model_cost.get(model)
    if entry is None:
        hits = [(k, v) for k, v in litellm.model_cost.items() if k.rsplit("/", 1)[-1] == model]
        if len(hits) != 1:
            return None
        key, entry = hits[0]

    in_price, out_price = entry.get("input_cost_per_token"), entry.get("output_cost_per_token")
    if in_price is None or out_price is None:
        return None
    return ModelPrices(
        in_price,
        out_price,
        f"litellm {_litellm_version()}'s bundled price map ({key!r}) — may be out of date",
    )


def _litellm_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("litellm")
    except PackageNotFoundError:  # pragma: no cover — litellm is a hard dependency
        return "?"


def extraction_prices() -> ModelPrices | None:
    """The extraction provider's prices: manual override, else litellm's bundled map.

    Both manual fields are required for the override, matching llm/chat.py and
    agents/decks.py — the two other places in the tree that price a call. A half-set
    override falls through to litellm rather than being partly honoured, so there is
    exactly one price *pair* in play and one source to name.
    """
    cfg = load_provider("extraction")
    if cfg is None:
        return None
    if cfg.input_price_per_mtok is not None and cfg.output_price_per_mtok is not None:
        return ModelPrices(
            cfg.input_price_per_mtok / 1_000_000,
            cfg.output_price_per_mtok / 1_000_000,
            "config.toml",
        )
    return _litellm_prices(cfg.model)


@dataclass(frozen=True)
class BuildEstimate:
    """What `groundly index --graph` prints before spending anything.

    A *range*, not a point, because output volume is a property of the model rather than
    the corpus and cannot be predicted from the corpus alone: measured on one 355-chunk
    build, completion:prompt was 0.87:1 on four runs and 4.06:1 on a fifth (a reasoning
    model). Both ends cover the extraction pass only — see `estimate_cost`.
    """

    input_tokens: int
    max_output_tokens: int
    low_usd: float | None
    high_usd: float | None
    price_source: str | None
    # Set when the configured model name is an unpinned alias, where price drift is
    # certain rather than merely possible. litellm 1.86.2 prices
    # `mistral/mistral-small-latest` at $0.06/$0.18 per Mtok; the alias resolves today to
    # Mistral Small 4 at $0.15/$0.60 — 2.5x and 3.3x low, silently.
    moving_alias: str | None


def _max_output_tokens_per_call() -> int:
    """The room an extraction call has left to answer in, once its own prompt is in the
    window. Derived, not fitted: at the 4096 default and the 696-token bundled prompt
    that is 2888 tokens, and it moves with `graph.context_window` the way the real
    ceiling does. A coefficient fitted to one provider's measured output would be wrong
    by ~4.7x on the next one (see BuildEstimate)."""
    from groundly.core.manifest import CHUNK_MAX_TOKENS

    window = load_settings().graph.context_window
    return max(0, window - _preamble_tokens() - CHUNK_MAX_TOKENS)


def estimate_cost(total_chars: int, chunk_count: int) -> BuildEstimate:
    """Rough heuristic graph-build cost estimate: no tokenizer, no LLM call. Uses
    `load_provider` (not `require_provider`) — this is an estimate, not the fail-fast
    build path, so an unconfigured provider degrades to an unpriced estimate.

    Every chunk is sent with the whole few-shot extraction preamble, which at Groundly's
    chunk size is the *majority* of the input — counting chunk text alone understated a
    real 1194-chunk build by 11.4x. Measured per call rather than at import: the prompt
    is configurable now, so a module constant would price the wrong one.

    **Both ends of the range price the extraction pass only.** `summarize_descriptions`
    and `create_community_reports` are billed on top and are genuinely unpredictable
    here: they are sized by the *extracted graph*, and the same 355-chunk corpus produced
    23 communities on one build and 436 on another. Disclosing that is the CLI's job
    (cli/subjects.py); inventing a number for it would be the same lie in a new place."""
    input_tokens = total_chars // 4 + chunk_count * _preamble_tokens()
    max_output_tokens = chunk_count * _max_output_tokens_per_call()

    cfg = load_provider("extraction")
    prices = extraction_prices()
    alias = cfg.model if cfg is not None and cfg.model.endswith("-latest") else None
    if prices is None:
        return BuildEstimate(input_tokens, max_output_tokens, None, None, None, alias)

    low = input_tokens * prices.input_per_token
    return BuildEstimate(
        input_tokens,
        max_output_tokens,
        low,
        low + max_output_tokens * prices.output_per_token,
        prices.source,
        alias,
    )
