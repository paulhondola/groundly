"""groundly/llm/embeddings.py: lazy bge-m3 embedder. Construction-failure wrapping
mirrors test_llm_rerank.py's pattern for BgeReranker."""

import builtins

import pytest

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
