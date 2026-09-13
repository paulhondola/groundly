from dataclasses import dataclass

from groundly.llm.config import load_provider, load_settings
from groundly.llm.graphrag_adapter import (
    ExtractionPromptError,
    ReadableMetricsStore,
    _bundled_prompt_text,
    resolve_extraction_prompt,
)


def _preamble_tokens() -> int:
    """The size of the extraction preamble sent with every chunk, from the prompt the build
    will use. Falls back to the bundled prompt when an override is unreadable: an estimate
    must degrade, and `build_graph` still names the failure before any LLM call.
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
    """Usage graphrag accumulated since `reset_metered_usage()`, summed over every model's
    metrics store (see `ReadableMetricsStore`) and priced.

    **Cache hits count in the token totals but were never billed**, and a retry against the
    preserved `cache/` is the normal path, so each store's cost is scaled by its own billed
    fraction. Returns None on anything unexpected: a printed number must never fail a
    finished build.
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
                # A store never called is skipped. One called that metered nothing (a
                # provider omitting `usage`) is missing information, so the total goes
                # unpriced rather than reading as $0.
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
    """Zero every metrics store a previous in-process build left, so `metered_usage()`
    reports only this build. The handles are kept: graphrag reuses those singleton stores,
    and a changed model or base_url gets a new store while the old one stays at zero."""
    for instance in ReadableMetricsStore.instances.values():
        instance.clear_metrics()


@dataclass(frozen=True)
class ModelPrices:
    """Per-token prices for one model, plus their source, which the spend gate prints so
    the student can sanity-check the price."""

    input_per_token: float
    output_per_token: float
    source: str


def _litellm_prices(model: str) -> ModelPrices | None:
    """litellm's bundled price map, looked up by the bare model name from config.toml.

    litellm keys OpenAI models bare but others with their provider
    (`groq/llama-3.3-70b-versatile`), and Groundly only knows the bare name, so fall back
    to a suffix match. Only a unique match counts, so an ambiguous name never bills
    against another provider's price. Both prices must be present, or the range's upper
    bound would silently omit output.
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

    The override needs both fields, as in llm/chat.py; a half-set one falls through to
    litellm, so one price pair and one source are ever in play.
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
    """Prices for one metrics store's model. `store_id` is `openai/<model>` for every
    store Groundly creates; the manual override applies when `[providers.extraction]` names
    that model, else litellm's map by bare name.
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
    """What `groundly index --graph` prints before spending anything. A range, because
    output volume depends on the model, not the corpus; both ends price the extraction
    pass only (see `estimate_cost`).
    """

    input_tokens: int
    max_output_tokens: int
    low_usd: float | None
    high_usd: float | None
    price_source: str | None
    # Set for an unpinned `*-latest` alias, which can resolve to a differently priced model
    # than litellm's bundled map records.
    moving_alias: str | None


def _max_output_tokens_per_call() -> int:
    """The room an extraction call has left to answer in once its prompt is in the window.
    Derived rather than fitted to one provider's output, so it tracks
    `graph.context_window`."""
    from groundly.core.manifest import CHUNK_MAX_TOKENS

    window = load_settings().graph.context_window
    return max(0, window - _preamble_tokens() - CHUNK_MAX_TOKENS)


def estimate_cost(total_chars: int, chunk_count: int) -> BuildEstimate:
    """Heuristic graph-build cost: no tokenizer, no LLM call. An unconfigured provider gives
    an unpriced estimate rather than an error.

    The extraction preamble is most of each call's input, so it is counted per chunk.
    **Both ends price the extraction pass only**: description summaries and community
    reports scale with the extracted graph and cannot be sized beforehand, which the CLI
    discloses (cli/cost_display.py)."""
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
