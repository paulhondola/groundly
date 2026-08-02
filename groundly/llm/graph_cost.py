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

    There can be more than one store: `graph.report_call_class` (core/config.GraphSettings)
    lets community reports run against a different model than extraction, and graphrag
    caches metrics stores as singletons keyed on hashed init args *including* the model id
    (see `ReadableMetricsStore`'s docstring) — so two models produce two stores, and
    summing only the most recent one would silently under-report everything the other
    model spent.

    **Cache hits are counted in the token totals but were never paid for** — a rebuild
    against a warm cache reported 420,965 prompt tokens at `cache_hit_rate: 1.0`, none of
    which cost anything. That is the normal path here, not an edge case: decision 21
    deliberately preserves `cache/` across a failed rebuild so the retry keeps the
    responses already bought. Tokens stay as metered (they were genuinely processed); the
    *cost* is scaled to the responses that actually reached the provider — computed PER
    STORE, because the billed fraction is a property of that store's own cache hits, not
    a global average across models with different cache behavior.

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
                # Registered but never *called* (no communities formed, so the report
                # model never ran) contributes nothing and can be skipped. Called and
                # metered nothing — a provider that omitted `usage` — is missing
                # information, not absent spend: skipping it silently would print the
                # other model's cost as if it were the whole bill. Decision 23's rule is
                # that an absence must never read as a fact.
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

    That reuse is keyed on *hashed init args* (graphrag_common/factory.create), so it only
    holds while the ModelConfig is unchanged. Tracking every instance rather than a single
    `latest` handle is what makes that survivable: change `extraction.model` or `base_url`
    between two in-process builds and graphrag constructs a *new* store, but the old one is
    cleared here and contributes zero, so `metered_usage()` reports the new build's real
    totals instead of None or a previous build's. That repairs the "one build per process"
    limitation this function used to carry — the reason the split needed it, since two
    completion models are two stores within a *single* build."""
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


def _prices_for_model(store_id: str) -> ModelPrices | None:
    """Prices for one metrics store's model, keyed off the store's own `id` rather than
    always `extraction` — `metered_usage`'s per-store generalization of
    `extraction_prices`, needed because `graph.report_call_class` can meter a second
    model under a different provider section entirely.

    `store_id` is graphrag's `model_provider/model` (`completion_model_config` always
    sets `model_provider="openai"`, so this is always `openai/<cfg.model>` for any store
    Groundly creates). Manual override first, matched against whichever of the two call
    classes a graph build can register a completion model under (extraction, or
    `report_call_class` when it differs) — else litellm's bundled map by bare model name,
    which is call-class-agnostic already.
    """
    bare_model = store_id.removeprefix("openai/")
    for call_class in ("extraction", load_settings().graph.report_call_class):
        cfg = load_provider(call_class)
        if cfg is not None and cfg.model == bare_model:
            if cfg.input_price_per_mtok is not None and cfg.output_price_per_mtok is not None:
                return ModelPrices(
                    cfg.input_price_per_mtok / 1_000_000,
                    cfg.output_price_per_mtok / 1_000_000,
                    "config.toml",
                )
            break
    return _litellm_prices(bare_model)


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
    # The call class serving community reports, when it is not `extraction`. The range
    # above prices the extraction pass only, which is a caveat when one provider does
    # everything and a *hole* when two do: in the split this exists to enable — local
    # extraction, cloud reports — every dollar the build spends is on this provider and
    # none of it is in the figure above. Naming it is all that can honestly be done;
    # sizing it would need the community count, which only exists after the build.
    report_call_class: str | None = None


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
    # None on the default path: reports run on the same provider the range already
    # prices, so there is no second bill to warn about.
    configured = load_settings().graph.report_call_class
    report_class = configured if configured != "extraction" else None
    alias = cfg.model if cfg is not None and cfg.model.endswith("-latest") else None
    if prices is None:
        return BuildEstimate(input_tokens, max_output_tokens, None, None, None, alias, report_class)

    low = input_tokens * prices.input_per_token
    return BuildEstimate(
        input_tokens,
        max_output_tokens,
        low,
        low + max_output_tokens * prices.output_per_token,
        prices.source,
        alias,
        report_class,
    )
