from dataclasses import dataclass

from groundly.llm.config import load_provider, load_settings
from groundly.llm.graphrag_adapter import (
    ExtractionPromptError,
    ReadableMetricsStore,
    _bundled_prompt_text,
    resolve_extraction_prompt,
)


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
    store = ReadableMetricsStore.latest
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
    if ReadableMetricsStore.latest is not None:
        ReadableMetricsStore.latest.clear_metrics()


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
