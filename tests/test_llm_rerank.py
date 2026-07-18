"""groundly/llm/rerank.py: lazy cross-encoder reranker. Fast tests only check
laziness (no model load on construction); real scoring is -m slow (test_slow_models.py)."""

import builtins

import pytest

from groundly.llm.rerank import RERANKER_HF_REVISION, RERANKER_MODEL, BgeReranker, Reranker


def test_bge_reranker_satisfies_the_reranker_protocol():
    assert hasattr(Reranker, "compute_score")
    assert callable(BgeReranker().compute_score)


def test_bge_reranker_does_not_load_model_on_construction(monkeypatch):
    def must_not_import(name, *a, **k):
        if name == "FlagEmbedding":
            raise AssertionError("FlagEmbedding must not be imported at construction")
        return real_import(name, *a, **k)

    import builtins

    real_import = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", must_not_import)
    BgeReranker()  # must not raise


def test_reranker_pin_is_a_hex_revision():
    assert RERANKER_MODEL == "BAAI/bge-reranker-v2-m3"
    assert len(RERANKER_HF_REVISION) == 40
    int(RERANKER_HF_REVISION, 16)  # valid hex sha


def test_bge_reranker_load_wraps_construction_failure_in_model_download_error(monkeypatch):
    from pathlib import Path

    from groundly.llm.embeddings import ModelDownloadError

    monkeypatch.setattr(
        "groundly.llm.embeddings.ensure_downloaded", lambda *a, **k: Path("/fake")
    )

    real_import = builtins.__import__

    def fail_flagembedding_import(name, *a, **k):
        if name == "FlagEmbedding":
            raise RuntimeError("boom")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fail_flagembedding_import)

    with pytest.raises(ModelDownloadError) as exc_info:
        BgeReranker()._load()
    assert RERANKER_MODEL in str(exc_info.value)
