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
    tokens, cost = estimate_cost(4000)
    assert tokens == 1000
    assert cost is None


def test_estimate_cost_unpriced_provider_returns_none_cost(home):
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "m"\n'
    )
    tokens, cost = estimate_cost(4000)
    assert tokens == 1000
    assert cost is None


def test_estimate_cost_priced_provider_computes_cost(home):
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "m"\ninput_price_per_mtok = 5.0\n'
    )
    tokens, cost = estimate_cost(4_000_000)  # 1,000,000 tokens
    assert tokens == 1_000_000
    assert cost == pytest.approx(5.0)
