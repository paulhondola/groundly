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


def test_estimate_cost_unconfigured_provider_returns_none_cost(home):
    tokens, cost = estimate_cost(4000, 0)
    assert tokens == 1000
    assert cost is None


def test_estimate_cost_unpriced_provider_returns_none_cost(home):
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "m"\n'
    )
    tokens, cost = estimate_cost(4000, 0)
    assert tokens == 1000
    assert cost is None


def test_estimate_cost_priced_provider_computes_cost(home):
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "m"\ninput_price_per_mtok = 5.0\n'
    )
    tokens, cost = estimate_cost(4_000_000, 0)  # 1,000,000 tokens
    assert tokens == 1_000_000
    assert cost == pytest.approx(5.0)


def test_estimate_cost_falls_back_to_litellm_map_for_mapped_model(monkeypatch, home):
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "gpt-4o-mini"\n'
    )
    import litellm

    monkeypatch.setattr(litellm, "model_cost", {"gpt-4o-mini": {"input_cost_per_token": 1.5e-07}})
    tokens, cost = estimate_cost(4_000_000, 0)  # 1,000,000 tokens
    assert tokens == 1_000_000
    assert cost == pytest.approx(0.15)


def test_estimate_cost_unmapped_model_in_litellm_map_returns_none(monkeypatch, home):
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "totally-unmapped-local-model"\n'
    )
    import litellm

    monkeypatch.setattr(litellm, "model_cost", {"gpt-4o-mini": {"input_cost_per_token": 1.5e-07}})
    tokens, cost = estimate_cost(4_000_000, 0)
    assert tokens == 1_000_000
    assert cost is None


def test_estimate_cost_manual_price_overrides_litellm_map(home):
    # No litellm stubbing needed: a configured manual price must short-circuit
    # before litellm's map is ever consulted.
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "gpt-4o-mini"\n'
        "input_price_per_mtok = 5.0\n"
    )
    tokens, cost = estimate_cost(4_000_000, 0)
    assert tokens == 1_000_000
    assert cost == pytest.approx(5.0)


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
    """graphrag sends its whole few-shot preamble with every chunk, which at Groundly's
    chunk size dominates the input — counting chunk text alone understated a real
    1194-chunk build by ~4x."""
    from groundly.llm.graphrag_adapter import _EXTRACTION_PREAMBLE_TOKENS

    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "m"\ninput_price_per_mtok = 1.0\n'
    )
    chunk_tokens = 512
    tokens, _ = estimate_cost(chunk_tokens * 4, 1)  # one chunk of 512 tokens

    assert tokens == chunk_tokens + _EXTRACTION_PREAMBLE_TOKENS
    assert tokens > 3 * chunk_tokens  # the preamble is the majority of every call


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
            "gpt-4o-mini": {"input_cost_per_token": 1.5e-07},
            "groq/llama-3.3-70b-versatile": {"input_cost_per_token": 5.9e-07},
        },
    )
    tokens, cost = estimate_cost(4_000_000, 0)
    assert tokens == 1_000_000
    assert cost == pytest.approx(0.59)


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
            "groq/llama-3.3-70b": {"input_cost_per_token": 5.9e-07},
            "together_ai/llama-3.3-70b": {"input_cost_per_token": 8.8e-07},
        },
    )
    tokens, cost = estimate_cost(4_000_000, 0)
    assert tokens == 1_000_000
    assert cost is None


def test_estimate_cost_prefers_an_exact_bare_key_over_a_suffix_match(monkeypatch, home):
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "some-model"\n'
    )
    import litellm

    monkeypatch.setattr(
        litellm,
        "model_cost",
        {
            "some-model": {"input_cost_per_token": 1e-06},
            "vendor/some-model": {"input_cost_per_token": 9e-06},
        },
    )
    _, cost = estimate_cost(4_000_000, 0)
    assert cost == pytest.approx(1.0)  # the exact key, not the prefixed one


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
