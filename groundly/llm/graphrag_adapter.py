"""Translates Groundly's provider config into graphrag's config primitives, the one place
that does so (LLM clients are constructed only in llm/). graphrag's LiteLLM client speaks
the same OpenAI-compatible base_url + model + key shape.
"""

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path
from urllib.parse import urlparse

from graphrag.config.defaults import graphrag_config_defaults

# litellm's env defaults (price map, log level) are set in groundly/__init__.py: callers
# like ingestion/graph.py import graphrag, and with it litellm, before this module.
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

# The keys graphrag looks completion_models/embedding_models up by — one definition,
# imported wherever a config names a model.
COMPLETION_MODEL_ID = "default_completion_model"
EMBEDDING_MODEL_ID = "default_embedding_model"

_BUNDLED_PROMPT = ("groundly", "prompts/extract_graph.txt")

# graphrag's extractor formats the prompt with exactly these two keys
# (graph_extractor._process_document) — nothing else is interpolated.
_REQUIRED_PLACEHOLDERS = ("{entity_types}", "{input_text}")

# ...and these are *not*, though they look it: graphrag 3.1.0 writes delimiters literally
# and parses with hardcoded constants. A prompt carrying one raises KeyError per chunk,
# which graphrag swallows, so every chunk fails silently. Reject them up front.
_FORBIDDEN_PLACEHOLDERS = ("{tuple_delimiter}", "{record_delimiter}", "{completion_delimiter}")


class ExtractionPromptError(Exception):
    """A configured `graph.extraction_prompt` that is missing, unreadable or malformed,
    raised before any LLM call. Defined in llm/ because ingestion/ sits above it.
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
    """Yield `(path, text)` for the extraction prompt the build will send, validated so a
    bad override is a named error before any LLM call.

    A path, not just text: graphrag reads `ExtractGraphConfig.prompt` from disk, so the
    file must exist for the whole build. `as_file()` makes that true for a zipped install
    too, hence the context manager.
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
    """sha256 over exactly what the build sends: the prompt text, the entity types as
    graphrag joins them (unsorted, since order changes the prompt), and the gleaning rounds
    (each one another extraction call per chunk). A change makes `graph_is_stale` offer a
    rebuild."""
    return hashlib.sha256(
        f"{prompt_text}\n{','.join(entity_types)}\ngleanings={gleanings}".encode()
    ).hexdigest()


# Set once by allow_nonstandard_service_tier(); see there for why this is a flag rather
# than a check against the field's current annotation.
_service_tier_widened = False


def completion_model_config(track_usage: bool = False) -> ModelConfig:
    """graphrag's ModelConfig from `[providers.extraction]`, which must be configured. A
    keyless local provider gets a placeholder key, because ModelConfig rejects an empty one.

    `track_usage` swaps in a metrics store this process can read back (see
    `metered_usage`). `reasoning_effort` goes under `call_args["extra_body"]`: passed flat,
    litellm raises UnsupportedParamsError on every call."""
    cfg = require_provider("extraction")
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
    """The `embedding_models` entry a graph build registers. The *key* is what graphrag
    resolves the embedder by (see EMBEDDING_MODEL_ID)."""
    return {
        EMBEDDING_MODEL_ID: ModelConfig(
            type=BGE_M3_EMBEDDING_TYPE,
            model_provider=BGE_M3_EMBEDDING_TYPE,
            model="bge-m3",
        )
    }


def graph_vector_store(graph_dir: Path) -> VectorStoreConfig:
    """Where a subject's graph keeps its LanceDB entity-description vectors — path and
    dimension defined once."""
    return VectorStoreConfig(db_uri=str(graph_dir / "lancedb"), vector_size=EMBEDDING_DIM)


class ReadableMetricsStore(MemoryMetricsStore):
    """graphrag_llm's in-memory metrics store, plus a handle on every instance.

    graphrag writes its usage only from an `atexit` hook, after the interpreter is done,
    but `get_metrics()` holds the same numbers live. Keyed by `id` (`model_provider/model`)
    because graphrag_llm caches stores as singletons keyed on init args including that id:
    a different model gets its own store, and `metered_usage` sums them all.
    """

    instances: "dict[str, ReadableMetricsStore]" = {}

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        type(self).instances[self.id] = self


def register_groundly_metrics_store() -> None:
    """Register `ReadableMetricsStore` under the `groundly` store name. Idempotent: the
    factory's register() is a plain dict assignment."""
    register_metrics_store(GROUNDLY_METRICS_STORE_TYPE, ReadableMetricsStore, "singleton")


def _retry_config() -> RetryConfig:
    """Always on: graphrag fires extraction concurrently and swallows a 429 per text unit,
    so without retries a rate-limited provider silently drops chunks. Jitter stops workers
    retrying in lockstep; base_delay must exceed 1.0 for exponential backoff.

    BadRequestError is dropped from graphrag_llm's never-retry list: a local runtime
    reports KV-cache exhaustion as a 400 that a backed-off retry recovers from.
    `concurrent_requests()` is the fix and this the backstop; a structural 400 costs ~1 min
    of retries, and the build's probe screens that case first. The private default is
    imported on purpose, so a pin bump that renames it fails loudly."""
    return RetryConfig(
        max_retries=5,
        base_delay=2.0,
        max_delay=60.0,
        jitter=True,
        exceptions_to_skip=[e for e in _default_exceptions_to_skip if e != "BadRequestError"],
    )


# graphrag has one concurrency setting for every stage, defaulting to 25.
_LOCAL_CONCURRENT_REQUESTS = 1

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


def _is_loopback(cfg: ProviderConfig) -> bool:
    host = urlparse(cfg.base_url).hostname
    return bool(host) and (host in _LOOPBACK_HOSTS or host.endswith(".localhost"))


def concurrent_requests(*cfgs: ProviderConfig) -> int:
    """How many calls a graph build keeps in flight: 1 if any given provider is loopback,
    else graphrag's default.

    prompt_budgets() sizes each request to `graph.context_window`, but a llama.cpp-family
    server serves concurrent requests from one shared KV cache of that size, and no
    OpenAI-compatible endpoint reports its slot count, so a local build serializes (cheap:
    the slots share one GPU anyway). A local runtime reached over the LAN looks remote and
    needs its slots reduced by hand (docs/guides/graphrag-provider.md)."""
    return (
        _LOCAL_CONCURRENT_REQUESTS
        if any(_is_loopback(cfg) for cfg in cfgs)
        else graphrag_config_defaults.concurrent_requests
    )


def _rate_limit_config(cfg: ProviderConfig) -> RateLimitConfig | None:
    """Only when the provider's limits are declared in config.toml: they cannot be
    discovered, and guessing would throttle a local runtime that has none."""
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
    """Scale graphrag's stage budgets to the configured context window.

    graphrag's defaults assume a large cloud context; on a small local model every call
    overflows, and graphrag swallows each failure, reporting an empty graph as a success.
    Each budget is `min(graphrag's default, a share of the window)`, so this only scales
    down. The budgets are per request; concurrent_requests() keeps a local runtime to one
    request so they hold against its shared KV cache.
    """
    return PromptBudgets(
        # The student's `graph.gleanings`, clamped by the window: a gleaning round re-sends
        # prompt, chunk and first answer, roughly doubling peak context, which does not fit
        # beside the stage budgets below 16384.
        max_gleanings=gleanings if context_window >= 16384 else 0,
        summarize_max_input_tokens=min(4000, context_window // 2),
        summarize_max_length=min(500, context_window // 4),
        community_max_input_length=min(8000, context_window // 2),
        community_max_length=min(2000, context_window // 4),
    )


class Bgem3GraphEmbedding(LLMEmbedding):
    """graphrag's entity-description embedder, delegated to the process-wide bge-m3: no new
    provider config and no marginal cost. Dense vectors only. `embedder` is None in
    production (lazily `shared_embedder()`) or a stub injected by tests.
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
    """Widen `graphrag_llm.LLMCompletionResponse.service_tier` from OpenAI's literal set
    to any string.

    graphrag_llm validates every response against OpenAI's exact enum, so a provider that
    reports its own tier (Groq's 'on_demand') has every paid-for response discarded at
    parse time. graphrag never reads the field, and the model is built in a closure with no
    override seam, so widening it is the available fix. Idempotent; OpenAI's values still
    validate. The guard is a module flag because `str | None` builds a new UnionType each
    time, so comparing annotations would re-run `model_rebuild(force=True)` on a shared
    third-party class every call.
    """
    global _service_tier_widened

    if _service_tier_widened:
        return

    from graphrag_llm.types.types import LLMCompletionResponse

    LLMCompletionResponse.model_fields["service_tier"].annotation = str | None
    LLMCompletionResponse.model_rebuild(force=True)
    _service_tier_widened = True


def register_bge_m3_embedding() -> None:
    """Register Bgem3GraphEmbedding under the `bge_m3` strategy name. Idempotent: the
    factory's register() is a plain dict assignment."""
    register_embedding(BGE_M3_EMBEDDING_TYPE, Bgem3GraphEmbedding)
