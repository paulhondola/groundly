"""groundly/llm/graphrag_adapter.py: the one place translating Groundly's provider
config into graphrag's config primitives (.claude/rules/architecture.md)."""

import pytest
from graphrag_llm.config import ModelConfig
from graphrag_llm.embedding.embedding_factory import embedding_factory

from groundly.core.config import ProviderNotConfiguredError
from groundly.llm.graphrag_adapter import (
    BGE_M3_EMBEDDING_TYPE,
    Bgem3GraphEmbedding,
    completion_model_config,
    estimate_cost,
    register_bge_m3_embedding,
)


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


class StubEmbedder:
    """Records the texts it was asked to encode; returns deterministic dense vectors
    and (unused) sparse weights, matching Embedder.encode's shape."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def encode(self, texts):
        self.calls.append(texts)
        dense = [[float(len(t)), 0.5] for t in texts]
        sparse = [{1: 0.9} for _ in texts]
        return dense, sparse


# --- completion_model_config --------------------------------------------------------


def test_completion_model_config_maps_extraction_provider(home):
    (home / "config.toml").write_text(
        "[providers.extraction]\n"
        'base_url = "http://localhost:1234/v1"\n'
        'model = "gpt-4o-mini"\n'
        'api_key = "sk-secret"\n'
    )
    cfg = completion_model_config()
    assert isinstance(cfg, ModelConfig)
    assert cfg.model_provider == "openai"
    assert cfg.model == "gpt-4o-mini"
    assert cfg.api_base == "http://localhost:1234/v1"
    assert cfg.api_key == "sk-secret"


def test_completion_model_config_fails_fast_when_unconfigured(home):
    with pytest.raises(ProviderNotConfiguredError):
        completion_model_config()


def test_completion_model_config_local_provider_gets_placeholder_key(home):
    # LM Studio/Ollama: no api_key configured, but graphrag's ModelConfig rejects an
    # empty one outright — a local provider still needs *some* truthy placeholder.
    (home / "config.toml").write_text(
        "[providers.extraction]\n"
        'base_url = "http://localhost:1234/v1"\n'
        'model = "gemma-4-12b-qat"\n'
        'api_key = ""\n'
    )
    cfg = completion_model_config()  # must not raise ModelConfig's own validation error
    assert cfg.api_key


# --- Bgem3GraphEmbedding -------------------------------------------------------------


def test_embedding_delegates_to_embedder_and_returns_dense_only():
    stub = StubEmbedder()
    adapter = Bgem3GraphEmbedding(model_id="bge_m3/bge-m3", embedder=stub)

    response = adapter.embedding(input=["hello", "world!"])

    assert stub.calls == [["hello", "world!"]]
    assert response.embeddings == [[5.0, 0.5], [6.0, 0.5]]
    assert response.model == "bge_m3/bge-m3"


async def test_embedding_async_delegates_the_same_way():
    stub = StubEmbedder()
    adapter = Bgem3GraphEmbedding(embedder=stub)

    response = await adapter.embedding_async(input=["hi"])

    assert response.embeddings == [[2.0, 0.5]]


def test_embedder_property_lazily_constructs_bge_m3_embedder(monkeypatch):
    import groundly.llm.graphrag_adapter as adapter_module

    class FakeBgeM3Embedder:
        def __init__(self):
            self.constructed = True

    monkeypatch.setattr("groundly.llm.embeddings.BgeM3Embedder", FakeBgeM3Embedder, raising=True)
    adapter = adapter_module.Bgem3GraphEmbedding()
    assert adapter._embedder is None  # not constructed yet
    embedder = adapter.embedder
    assert isinstance(embedder, FakeBgeM3Embedder)
    assert adapter.embedder is embedder  # constructed once, cached


def test_metrics_store_and_tokenizer_are_passthrough():
    adapter = Bgem3GraphEmbedding(tokenizer="tok", metrics_store="metrics")
    assert adapter.tokenizer == "tok"
    assert adapter.metrics_store == "metrics"


# --- register_bge_m3_embedding -------------------------------------------------------


def test_register_bge_m3_embedding_is_idempotent():
    register_bge_m3_embedding()
    register_bge_m3_embedding()  # must not raise
    assert BGE_M3_EMBEDDING_TYPE in embedding_factory


# --- estimate_cost -------------------------------------------------------------------


def _priced(**extra: str) -> str:
    """An extraction provider with both manual prices set. Both are required for the
    override (matching llm/chat.py and agents/decks.py), so tests that want a priced
    estimate have to say so in full."""
    body = (
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "m"\n'
        "input_price_per_mtok = 5.0\noutput_price_per_mtok = 10.0\n"
    )
    return body + "".join(f"{k} = {v}\n" for k, v in extra.items())


def test_estimate_cost_unconfigured_provider_returns_none_cost(home):
    est = estimate_cost(4000, 0)
    assert est.input_tokens == 1000
    assert est.low_usd is None and est.high_usd is None
    assert est.price_source is None


def test_estimate_cost_unpriced_provider_returns_none_cost(home):
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "m"\n'
    )
    est = estimate_cost(4000, 0)
    assert est.input_tokens == 1000
    assert est.low_usd is None


def test_estimate_cost_priced_provider_computes_cost(home):
    (home / "config.toml").write_text(_priced())
    est = estimate_cost(4_000_000, 0)  # 1,000,000 input tokens, no chunks
    assert est.input_tokens == 1_000_000
    assert est.low_usd == pytest.approx(5.0)
    assert est.price_source == "config.toml"


def test_estimate_cost_half_set_manual_prices_fall_through_to_litellm(monkeypatch, home):
    """A half-set override would produce a range whose upper bound silently omits
    output — the exact defect the range exists to fix. Both fields or neither."""
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "gpt-4o-mini"\n'
        "input_price_per_mtok = 5.0\n"
    )
    import litellm

    monkeypatch.setattr(
        litellm,
        "model_cost",
        {"gpt-4o-mini": {"input_cost_per_token": 1.5e-07, "output_cost_per_token": 6e-07}},
    )
    est = estimate_cost(4_000_000, 0)
    assert est.low_usd == pytest.approx(0.15)  # litellm's, not the manual 5.0
    assert est.price_source.startswith("litellm ")


def test_estimate_cost_prices_output_tokens_too(home):
    """The whole point: the previous estimate priced input only, understating a build
    several-fold on its own (measured completion:prompt ran from 0.87:1 to 4.06:1)."""
    (home / "config.toml").write_text(_priced())
    est = estimate_cost(4_000_000, 10)
    assert est.max_output_tokens > 0
    assert est.high_usd == pytest.approx(est.low_usd + est.max_output_tokens * 10.0 / 1_000_000)
    assert est.high_usd > est.low_usd


def test_estimate_cost_output_ceiling_scales_with_the_context_window(home):
    """Derived from the room a call has left to answer in, not fitted to one provider's
    measured output — so it tracks graph.context_window instead of going stale."""
    from groundly.llm.graphrag_adapter import _preamble_tokens

    (home / "config.toml").write_text(_priced() + "\n[graph]\ncontext_window = 32768\n")
    wide = estimate_cost(0, 1)
    assert wide.max_output_tokens == 32768 - _preamble_tokens() - 512

    (home / "config.toml").write_text(_priced() + "\n[graph]\ncontext_window = 4096\n")
    assert estimate_cost(0, 1).max_output_tokens < wide.max_output_tokens


def test_estimate_cost_output_ceiling_never_goes_negative(home, tmp_path):
    """Reachable via graph.extraction_prompt: a custom preamble larger than the window
    leaves no room to answer in. Zero, not a negative estimate that would price the
    high end *below* the low one. (build_graph refuses this config separately.)"""
    custom = tmp_path / "huge.txt"
    custom.write_text("Types [{entity_types}] Text {input_text}" + "x" * 40_000)
    (home / "config.toml").write_text(
        _priced() + f'\n[graph]\ncontext_window = 2048\nextraction_prompt = "{custom}"\n'
    )
    est = estimate_cost(0, 100)
    assert est.max_output_tokens == 0
    assert est.high_usd == est.low_usd


def test_estimate_cost_flags_a_moving_alias(home):
    """litellm 1.86.2 prices mistral/mistral-small-latest at $0.06/$0.18 per Mtok; the
    alias resolves today to Mistral Small 4 at $0.15/$0.60. Drift is certain here, not
    merely possible, so the CLI gets something specific to warn about."""
    (home / "config.toml").write_text(
        _priced().replace('model = "m"', 'model = "mistral-small-latest"')
    )
    assert estimate_cost(4000, 0).moving_alias == "mistral-small-latest"


def test_estimate_cost_pinned_model_is_not_flagged(home):
    (home / "config.toml").write_text(
        _priced().replace('model = "m"', 'model = "mistral-small-2603"')
    )
    assert estimate_cost(4000, 0).moving_alias is None


def test_estimate_cost_falls_back_to_litellm_map_for_mapped_model(monkeypatch, home):
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "gpt-4o-mini"\n'
    )
    import litellm

    monkeypatch.setattr(
        litellm,
        "model_cost",
        {"gpt-4o-mini": {"input_cost_per_token": 1.5e-07, "output_cost_per_token": 6e-07}},
    )
    est = estimate_cost(4_000_000, 0)  # 1,000,000 tokens
    assert est.input_tokens == 1_000_000
    assert est.low_usd == pytest.approx(0.15)
    assert "gpt-4o-mini" in est.price_source


def test_estimate_cost_half_priced_litellm_entry_is_refused(monkeypatch, home):
    """An entry with no output price would produce an upper bound identical to the
    lower one — a range that silently claims output is free."""
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "gpt-4o-mini"\n'
    )
    import litellm

    monkeypatch.setattr(litellm, "model_cost", {"gpt-4o-mini": {"input_cost_per_token": 1.5e-07}})
    assert estimate_cost(4_000_000, 0).low_usd is None


def test_estimate_cost_unmapped_model_in_litellm_map_returns_none(monkeypatch, home):
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "totally-unmapped-local-model"\n'
    )
    import litellm

    monkeypatch.setattr(
        litellm,
        "model_cost",
        {"gpt-4o-mini": {"input_cost_per_token": 1.5e-07, "output_cost_per_token": 6e-07}},
    )
    est = estimate_cost(4_000_000, 0)
    assert est.input_tokens == 1_000_000
    assert est.low_usd is None


def test_estimate_cost_manual_price_overrides_litellm_map(home):
    # No litellm stubbing needed: a configured manual price must short-circuit
    # before litellm's map is ever consulted.
    (home / "config.toml").write_text(_priced().replace('model = "m"', 'model = "gpt-4o-mini"'))
    est = estimate_cost(4_000_000, 0)
    assert est.input_tokens == 1_000_000
    assert est.low_usd == pytest.approx(5.0)
    assert est.price_source == "config.toml"


# --- prompt budgets ---------------------------------------------------------------------


@pytest.mark.parametrize("window", [4096, 8192, 16384, 32768, 131072])
def test_prompt_budgets_always_fit_the_window(window):
    """Every stage's input + output reserve has to fit the model's actual context —
    graphrag's own defaults want ~10k for community reports alone, which is what
    made every extraction call 400 on a 4k local model."""
    from groundly.llm.graphrag_adapter import prompt_budgets

    b = prompt_budgets(window)
    assert b.summarize_max_input_tokens + b.summarize_max_length <= window
    assert b.community_max_input_length + b.community_max_length <= window


@pytest.mark.parametrize("window", [4096, 8192, 16384, 131072])
def test_prompt_budgets_never_exceed_graphrag_defaults(window):
    """This only ever scales down: a big context reproduces stock graphrag."""
    from groundly.llm.graphrag_adapter import prompt_budgets

    b = prompt_budgets(window)
    assert b.summarize_max_input_tokens <= 4000
    assert b.summarize_max_length <= 500
    assert b.community_max_input_length <= 8000
    assert b.community_max_length <= 2000


def test_prompt_budgets_enable_gleanings_only_on_a_large_window():
    """The gleaning round replays prompt + chunk + the model's whole first answer,
    roughly doubling peak context for little gain on a <=512-token chunk."""
    from groundly.llm.graphrag_adapter import prompt_budgets

    assert prompt_budgets(4096).max_gleanings == 0
    assert prompt_budgets(8192).max_gleanings == 0
    assert prompt_budgets(16384).max_gleanings == 1
    assert prompt_budgets(131072).max_gleanings == 1


def test_estimate_cost_counts_the_per_chunk_extraction_preamble(home):
    """The whole few-shot preamble is sent with every chunk, which at Groundly's chunk
    size dominates the input — counting chunk text alone understated a real 1194-chunk
    build by 11.4x."""
    from groundly.llm.graphrag_adapter import _preamble_tokens

    (home / "config.toml").write_text(_priced())
    chunk_tokens = 512
    est = estimate_cost(chunk_tokens * 4, 1)  # one chunk of 512 tokens

    assert est.input_tokens == chunk_tokens + _preamble_tokens()


def test_estimate_cost_prices_the_bundled_prompt_not_graphrags(home):
    """The saving only reaches the student if the confirmation gate quotes it. Pricing
    graphrag's 1620-token preamble here would over-quote every build by ~2x."""
    from graphrag.prompts.index.extract_graph import GRAPH_EXTRACTION_PROMPT

    from groundly.llm.graphrag_adapter import _bundled_prompt_text, _preamble_tokens

    assert _preamble_tokens() == len(_bundled_prompt_text()) // 4
    assert _preamble_tokens() < len(GRAPH_EXTRACTION_PROMPT) // 4 / 2


def test_estimate_cost_measures_a_custom_prompt(home, tmp_path):
    custom = tmp_path / "custom.txt"
    custom.write_text("Types [{entity_types}] Text {input_text} Output:" + "x" * 4000)
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "m"\n'
        f'\n[graph]\nextraction_prompt = "{custom}"\n'
    )
    from groundly.llm.graphrag_adapter import _preamble_tokens

    assert _preamble_tokens() == len(custom.read_text()) // 4


def test_estimate_cost_falls_back_when_a_custom_prompt_is_unreadable(home, tmp_path):
    """estimate_cost feeds the cost line, not the build. It must degrade to a number
    rather than raise — build_graph still refuses, with a named cause, before any call."""
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "m"\n'
        f'\n[graph]\nextraction_prompt = "{tmp_path / "nope.txt"}"\n'
    )
    from groundly.llm.graphrag_adapter import _bundled_prompt_text, _preamble_tokens

    assert _preamble_tokens() == len(_bundled_prompt_text()) // 4
    assert estimate_cost(2048, 1).input_tokens > 0


def test_estimate_cost_prices_a_provider_prefixed_model_by_suffix(monkeypatch, home):
    """litellm keys OpenAI bare but everything else provider-prefixed. Groundly only
    knows the bare name (the provider is a base_url), so a plain .get() missed all
    2199 prefixed entries — including every Groq model."""
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "https://api.groq.com/openai/v1"\n'
        'model = "llama-3.3-70b-versatile"\n'
    )
    import litellm

    monkeypatch.setattr(
        litellm,
        "model_cost",
        {
            "gpt-4o-mini": {"input_cost_per_token": 1.5e-07, "output_cost_per_token": 6e-07},
            "groq/llama-3.3-70b-versatile": {
                "input_cost_per_token": 5.9e-07,
                "output_cost_per_token": 7.9e-07,
            },
        },
    )
    est = estimate_cost(4_000_000, 0)
    assert est.input_tokens == 1_000_000
    assert est.low_usd == pytest.approx(0.59)
    assert "groq/llama-3.3-70b-versatile" in est.price_source


def test_estimate_cost_refuses_an_ambiguous_suffix_match(monkeypatch, home):
    """Two providers shipping the same bare name must not silently bill against
    whichever happens to come first in the map."""
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "llama-3.3-70b"\n'
    )
    import litellm

    monkeypatch.setattr(
        litellm,
        "model_cost",
        {
            "groq/llama-3.3-70b": {
                "input_cost_per_token": 5.9e-07,
                "output_cost_per_token": 7.9e-07,
            },
            "together_ai/llama-3.3-70b": {
                "input_cost_per_token": 8.8e-07,
                "output_cost_per_token": 8.8e-07,
            },
        },
    )
    est = estimate_cost(4_000_000, 0)
    assert est.input_tokens == 1_000_000
    assert est.low_usd is None


def test_estimate_cost_prefers_an_exact_bare_key_over_a_suffix_match(monkeypatch, home):
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "some-model"\n'
    )
    import litellm

    monkeypatch.setattr(
        litellm,
        "model_cost",
        {
            "some-model": {"input_cost_per_token": 1e-06, "output_cost_per_token": 2e-06},
            "vendor/some-model": {"input_cost_per_token": 9e-06, "output_cost_per_token": 9e-06},
        },
    )
    est = estimate_cost(4_000_000, 0)
    assert est.low_usd == pytest.approx(1.0)  # the exact key, not the prefixed one


# --- provider response compatibility ----------------------------------------------------


def test_allow_nonstandard_service_tier_accepts_groqs_value():
    """graphrag_llm types service_tier with OpenAI's exact literal set and builds its
    response as LLMCompletionResponse(**response.model_dump()). Groq returns
    'on_demand', so pydantic rejected *every* response — HTTP 200, tokens spent,
    result discarded. Observed as 258 requests / 258 failures / no entities."""
    from graphrag_llm.types.types import LLMCompletionResponse

    from groundly.llm.graphrag_adapter import allow_nonstandard_service_tier

    allow_nonstandard_service_tier()
    payload = dict(id="x", created=1, model="m", object="chat.completion", choices=[])

    assert LLMCompletionResponse(**payload, service_tier="on_demand").service_tier == "on_demand"
    # must not break the providers that do follow OpenAI's enum
    assert LLMCompletionResponse(**payload, service_tier="default").service_tier == "default"
    assert LLMCompletionResponse(**payload, service_tier=None).service_tier is None


def test_allow_nonstandard_service_tier_is_idempotent():
    """Called on every build and every graph query, so it must be cheap to repeat."""
    from graphrag_llm.types.types import LLMCompletionResponse

    from groundly.llm.graphrag_adapter import allow_nonstandard_service_tier

    allow_nonstandard_service_tier()
    allow_nonstandard_service_tier()
    payload = dict(id="x", created=1, model="m", object="chat.completion", choices=[])
    assert LLMCompletionResponse(**payload, service_tier="on_demand").service_tier == "on_demand"


# --- retry / rate limiting ---------------------------------------------------------------


def _write_extraction(home, extra: str = "") -> None:
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "https://api.groq.com/openai/v1"\n'
        'model = "llama-3.3-70b-versatile"\napi_key = "k"\n' + extra
    )


def test_completion_model_config_always_retries_with_jittered_backoff(home):
    """graphrag swallows a 429 per text unit like any other failure, so without a retry
    a rate-limited provider silently drops chunks (283 of 304 against Groq's free tier).
    Jitter matters as much as backoff: without it every concurrent worker retries in
    lockstep and rebuilds the burst that caused the 429."""
    _write_extraction(home)
    retry = completion_model_config().retry

    assert retry is not None
    assert retry.max_retries == 5
    assert retry.jitter is True
    assert retry.base_delay > 1.0  # graphrag rejects <= 1.0 for exponential backoff


def test_completion_model_config_leaves_rate_limit_unset_by_default(home):
    """Unset means unthrottled — correct for a local runtime, which has no limits, and
    preserves the previous behavior for anyone who hasn't declared their tier."""
    _write_extraction(home)
    assert completion_model_config().rate_limit is None


def test_completion_model_config_builds_a_per_minute_rate_limit(home):
    _write_extraction(home, "requests_per_minute = 30\ntokens_per_minute = 6000\n")
    rl = completion_model_config().rate_limit

    assert rl is not None
    assert rl.period_in_seconds == 60
    assert rl.requests_per_period == 30
    assert rl.tokens_per_period == 6000


def test_rate_limit_honours_either_limit_alone(home):
    """Providers publish RPM and TPM independently; declaring one must not require
    inventing the other."""
    _write_extraction(home, "tokens_per_minute = 6000\n")
    rl = completion_model_config().rate_limit
    assert rl is not None and rl.tokens_per_period == 6000 and rl.requests_per_period is None

    _write_extraction(home, "requests_per_minute = 30\n")
    rl = completion_model_config().rate_limit
    assert rl is not None and rl.requests_per_period == 30 and rl.tokens_per_period is None


def test_allow_nonstandard_service_tier_rebuilds_the_model_only_once(monkeypatch):
    """Called on every build *and every graph query*. `str | None` builds a fresh
    types.UnionType each evaluation, so a guard of `field.annotation is not (str | None)`
    is always true and re-ran model_rebuild(force=True) on a shared third-party class
    from concurrent query handlers — pydantic promises nothing about that."""
    from graphrag_llm.types.types import LLMCompletionResponse

    from groundly.llm import graphrag_adapter

    monkeypatch.setattr(graphrag_adapter, "_service_tier_widened", False)
    rebuilds = []
    real = LLMCompletionResponse.model_rebuild
    monkeypatch.setattr(
        LLMCompletionResponse,
        "model_rebuild",
        classmethod(lambda cls, **kw: rebuilds.append(kw) or real(**kw)),
    )

    for _ in range(5):
        graphrag_adapter.allow_nonstandard_service_tier()

    assert len(rebuilds) == 1


# --- the bundled extraction prompt (decision 22) ----------------------------------------


def test_bundled_prompt_keeps_the_placeholders_graphrag_substitutes():
    """graph_extractor._process_document formats with exactly these two keys. A prompt
    missing one sends every chunk the same literal text."""
    from groundly.llm.graphrag_adapter import _bundled_prompt_text

    text = _bundled_prompt_text()
    assert "{entity_types}" in text
    assert "{input_text}" in text


def test_bundled_prompt_has_no_delimiter_placeholders():
    """graphrag 3.1.0 writes the delimiters into the prompt literally and parses with
    hardcoded constants — it never substitutes these. One in the prompt raises KeyError
    inside graph_extractor's per-chunk except, i.e. every chunk fails silently."""
    from groundly.llm.graphrag_adapter import _bundled_prompt_text

    text = _bundled_prompt_text()
    for placeholder in ("{tuple_delimiter}", "{record_delimiter}", "{completion_delimiter}"):
        assert placeholder not in text
    # ...and the literal delimiters the parser does split on are present
    assert "<|>" in text and "##" in text and "<|COMPLETE|>" in text


def test_bundled_prompt_stays_within_its_token_budget():
    """The whole point of decision 22: this preamble is sent once per chunk, so its size
    *is* the build's bill. 700 tokens keeps a 1194-chunk apd build near 1.0M (from
    2.12M). Growing the worked example past this silently re-inflates every build."""
    from groundly.llm.graphrag_adapter import _bundled_prompt_text

    assert len(_bundled_prompt_text()) // 4 <= 700


def test_bundled_prompt_reuses_graphrags_instruction_block_verbatim():
    """Only the worked examples are ours. The instruction block defines the record
    format the downstream parser depends on, so it is copied byte-for-byte — this fails
    if a graphrag upgrade changes it and the bundled prompt is not re-derived."""
    from graphrag.prompts.index.extract_graph import GRAPH_EXTRACTION_PROMPT

    from groundly.llm.graphrag_adapter import _bundled_prompt_text

    instructions, _, rest = GRAPH_EXTRACTION_PROMPT.partition("######################\n-Examples-")
    _, _, real_data = rest.partition("######################\n-Real Data-")

    text = _bundled_prompt_text()
    assert text.startswith(instructions)
    assert text.endswith(real_data)


def test_default_entity_types_target_course_material():
    """graphrag's defaults are organization/person/geo/event, which produced 75
    ORGANIZATION and 34 EVENT entities on a parallel-algorithms corpus."""
    from groundly.llm.graphrag_adapter import extraction_entity_types

    types = extraction_entity_types()
    assert "concept" in types and "algorithm" in types
    assert "person" in types  # courses cite Dijkstra and Lamport
    for news_type in ("organization", "geo", "event"):
        assert news_type not in types


def test_entity_types_are_split_and_stripped(home):
    from groundly.llm.graphrag_adapter import extraction_entity_types

    (home / "config.toml").write_text('[graph]\nentity_types = "concept, algorithm ,, theorem"\n')
    assert extraction_entity_types() == ["concept", "algorithm", "theorem"]


def test_custom_prompt_missing_file_names_the_cause(home, tmp_path):
    from groundly.llm.graphrag_adapter import ExtractionPromptError, resolve_extraction_prompt

    missing = tmp_path / "nope.txt"
    (home / "config.toml").write_text(f'[graph]\nextraction_prompt = "{missing}"\n')
    with pytest.raises(ExtractionPromptError) as exc:
        with resolve_extraction_prompt():
            pass
    assert str(missing) in str(exc.value)


def test_custom_prompt_missing_a_placeholder_names_it(home, tmp_path):
    from groundly.llm.graphrag_adapter import ExtractionPromptError, resolve_extraction_prompt

    custom = tmp_path / "custom.txt"
    custom.write_text("Types: [{entity_types}]\nOutput:")  # no {input_text}
    (home / "config.toml").write_text(f'[graph]\nextraction_prompt = "{custom}"\n')
    with pytest.raises(ExtractionPromptError, match=r"\{input_text\}"):
        with resolve_extraction_prompt():
            pass


def test_custom_prompt_with_a_delimiter_placeholder_is_refused(home, tmp_path):
    """The silent-failure case: graphrag does not substitute these, so .format() raises
    KeyError per chunk and swallows it. Refuse up front instead."""
    from groundly.llm.graphrag_adapter import ExtractionPromptError, resolve_extraction_prompt

    custom = tmp_path / "custom.txt"
    custom.write_text("Types [{entity_types}] Text {input_text} Sep {tuple_delimiter} Output:")
    (home / "config.toml").write_text(f'[graph]\nextraction_prompt = "{custom}"\n')
    with pytest.raises(ExtractionPromptError, match=r"tuple_delimiter"):
        with resolve_extraction_prompt():
            pass


def test_resolved_prompt_is_a_real_readable_file(home):
    """ExtractGraphConfig.prompt is a path graphrag re-reads at extraction time, so the
    resolved file has to exist on disk for the duration of the build."""
    from groundly.llm.graphrag_adapter import _bundled_prompt_text, resolve_extraction_prompt

    with resolve_extraction_prompt() as (path, text):
        assert path.is_file()
        assert path.read_text(encoding="utf-8") == text == _bundled_prompt_text()


def test_fingerprint_changes_with_prompt_and_with_types():
    from groundly.llm.graphrag_adapter import extraction_fingerprint

    base = extraction_fingerprint("prompt", ["concept", "algorithm"])
    assert base == extraction_fingerprint("prompt", ["concept", "algorithm"])
    assert base != extraction_fingerprint("other prompt", ["concept", "algorithm"])
    assert base != extraction_fingerprint("prompt", ["concept"])
    # order counts: graphrag interpolates the list as given, so a reorder is a change
    assert base != extraction_fingerprint("prompt", ["algorithm", "concept"])
