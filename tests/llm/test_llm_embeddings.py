"""groundly/llm/embeddings.py: lazy bge-m3 embedder. Construction-failure wrapping
mirrors test_llm_rerank.py's pattern for BgeReranker."""

import builtins
from pathlib import Path

import pytest

import groundly.llm.embeddings as embeddings_mod
from groundly.core.store import SubjectStore
from groundly.llm.embeddings import EMBEDDING_MODEL, BgeM3Embedder, ModelDownloadError


def test_bge_m3_load_wraps_construction_failure_in_model_download_error(monkeypatch):
    from pathlib import Path

    monkeypatch.setattr("groundly.llm.embeddings.ensure_downloaded", lambda *a, **k: Path("/fake"))

    real_import = builtins.__import__

    def fail_flagembedding_import(name, *a, **k):
        if name == "FlagEmbedding":
            raise RuntimeError("boom")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fail_flagembedding_import)

    with pytest.raises(ModelDownloadError) as exc_info:
        BgeM3Embedder()._load()
    assert EMBEDDING_MODEL in str(exc_info.value)


def test_shared_embedder_is_a_process_singleton_used_by_vector_retriever_default(monkeypatch):
    """One resident bge-m3 model shared by every default production call site, not a
    fresh instance per retriever/per call (performance fix: avoids ~1.15 GB of
    concurrent duplicate models on ask()'s multi-hop path)."""
    from groundly.retrieval.vector import VectorRetriever

    monkeypatch.setattr(embeddings_mod, "_shared", None)
    fake_instance = object()
    monkeypatch.setattr(embeddings_mod, "BgeM3Embedder", lambda: fake_instance)

    first = embeddings_mod.shared_embedder()
    second = embeddings_mod.shared_embedder()
    assert first is second is fake_instance

    store = SubjectStore(Path("/nonexistent/store.db"))
    retriever = VectorRetriever(store, rerank=False, context_k=1)
    assert retriever.embedder is fake_instance
