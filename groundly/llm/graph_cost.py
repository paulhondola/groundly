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
    """The usage graphrag accumulated since `reset_metered_usage()`, summed across every
    metered completion model and priced.

    Summed rather than read from one store: graphrag caches metrics stores as singletons
    keyed on hashed init args *including* the model id (see `ReadableMetricsStore`), so
    each model gets its own store.

    **Cache hits are counted in the token totals but were never paid for**, and a warm
    cache is the normal path: ingestion/graph.py preserves `cache/` across a failed rebuild
    so the retry keeps the responses already bought. Tokens stay as metered (they were
    genuinely processed); the *cost* is scaled to the responses that actually reached the
    provider — computed PER STORE, because the billed fraction is a property of that
    store's own cache hits.

    Returns None on anything unexpected. This is a number printed after a successful
    build — it must never be the reason one fails.
    """
    stores = list(ReadableMetricsStore.instances.values())
    if not stores:
        return None
    try:
        prompt_tokens = 0
        completion_tokens = 0
        cost_usd = 0.0
        priced = True
        for store in stores:
            metrics = store.get_metrics()
            store_prompt = int(metrics.get("prompt_tokens", 0))
            store_completion = int(metrics.get("completion_tokens", 0))
            if store_prompt == 0 and store_completion == 0:
                # Two different things reach zero tokens, and only one is harmless.
                # Registered but never *called* contributes nothing and can be skipped.
                # Called and metered nothing — a provider that omitted `usage` — is
                # missing information, not absent spend, and an absence must never read
                # as a fact: the total goes unpriced.
                if int(metrics.get("attempted_request_count", 0)) > 0:
                    priced = False
                continue
            prompt_tokens += store_prompt
            completion_tokens += store_completion

            responses = int(metrics.get("responses_with_tokens", 0))
            cached = int(metrics.get("cached_responses", 0))
            prices = _prices_for_model(store.id)
            if prices is None or responses == 0:
                priced = False
                continue
            billed = max(0, responses - cached) / responses
            cost_usd += billed * (
                store_prompt * prices.input_per_token + store_completion * prices.output_per_token
            )
    except Exception:  # noqa: BLE001 — see the docstring: never fail a finished build
        return None

    # A store that metered nothing has nothing to report — and saying "0 tokens, $0.00"
    # would read as a fact rather than as an absence.
    if prompt_tokens + completion_tokens == 0:
        return None

    return MeteredUsage(
        prompt_tokens,
        completion_tokens,
        prompt_tokens + completion_tokens,
        cost_usd if priced else None,
    )


def reset_metered_usage() -> None:
    """Zero every store a previous build left behind, so `metered_usage()` can only ever
    return this build's numbers. graphrag registers stores as singletons, so a repeat
    build in the same process reuses the same store(s) and would otherwise keep
    accumulating into their totals. The handles are deliberately *not* dropped — those
    reused stores are the ones the repeat build writes into.

    That reuse is keyed on *hashed init args* (graphrag_common/factory.create), so change
    `extraction.model` or `base_url` between two in-process builds and graphrag constructs
    a *new* store; the old one is cleared here and contributes zero."""
    for instance in ReadableMetricsStore.instances.values():
        instance.clear_metrics()


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
    provider (`groq/llama-3.3-70b-versatile`, `mistral/mistral-large-latest`). Groundly
    only ever knows the bare name, because the
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

    Both manual fields are required for the override, matching llm/chat.py. A half-set
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


def _prices_for_model(store_id: str) -> ModelPrices | None:
    """Prices for one metrics store's model, keyed off the store's own `id`.

    `store_id` is graphrag's `model_provider/model` (`completion_model_config` always
    sets `model_provider="openai"`, so this is `openai/<cfg.model>` for any store Groundly
    creates). Manual override when `[providers.extraction]` names this model, else
    litellm's bundled map by bare model name.
    """
    bare_model = store_id.removeprefix("openai/")
    cfg = load_provider("extraction")
    if (
        cfg is not None
        and cfg.model == bare_model
        and cfg.input_price_per_mtok is not None
        and cfg.output_price_per_mtok is not None
    ):
        return ModelPrices(
            cfg.input_price_per_mtok / 1_000_000,
            cfg.output_price_per_mtok / 1_000_000,
            "config.toml",
        )
    return _litellm_prices(bare_model)


@dataclass(frozen=True)
class BuildEstimate:
    """What `groundly index --graph` prints before spending anything.

    A *range*, not a point, because output volume is a property of the model rather than
    the corpus and cannot be predicted from the corpus alone (a reasoning model emits
    several times more). Both ends cover the extraction pass only — see `estimate_cost`.
    """

    input_tokens: int
    max_output_tokens: int
    low_usd: float | None
    high_usd: float | None
    price_source: str | None
    # Set when the configured model name is an unpinned alias (`*-latest`), where price
    # drift is certain rather than merely possible: the alias can resolve to a newer,
    # differently priced model than litellm's bundled map records.
    moving_alias: str | None


def _max_output_tokens_per_call() -> int:
    """The room an extraction call has left to answer in, once its own prompt is in the
    window. Derived, not fitted: it moves with `graph.context_window` the way the real
    ceiling does, where a coefficient fitted to one provider's output would be wrong on
    the next (see BuildEstimate)."""
    from groundly.core.manifest import CHUNK_MAX_TOKENS

    window = load_settings().graph.context_window
    return max(0, window - _preamble_tokens() - CHUNK_MAX_TOKENS)


def estimate_cost(total_chars: int, chunk_count: int) -> BuildEstimate:
    """Rough heuristic graph-build cost estimate: no tokenizer, no LLM call. Uses
    `load_provider` (not `require_provider`) — this is an estimate, not the fail-fast
    build path, so an unconfigured provider degrades to an unpriced estimate.

    Every chunk is sent with the whole few-shot extraction preamble, which at Groundly's
    chunk size is the *majority* of the input, so it is counted per chunk — measured per
    call rather than at import, because the prompt is configurable.

    **Both ends of the range price the extraction pass only.** `summarize_descriptions`
    and `create_community_reports` are billed on top and are sized by the *extracted
    graph*, which varies widely between builds of one corpus. Disclosing that is the CLI's
    job (cli/cost_display.py); inventing a number for it would be the same lie in a new
    place."""
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
