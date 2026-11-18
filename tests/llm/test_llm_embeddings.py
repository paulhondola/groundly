"""groundly/llm/embeddings.py: lazy bge-m3 embedder. Construction-failure wrapping
mirrors test_llm_rerank.py's pattern for BgeReranker."""

import builtins
import time
import weakref
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

    with pytest.raises(ModelDownloadError) as exc_info, BgeM3Embedder()._use():
        pass
    assert EMBEDDING_MODEL in str(exc_info.value)


def test_shared_embedder_is_a_process_singleton_used_by_vector_retriever_default(monkeypatch):
    """One resident bge-m3 model shared by every default production call site, not a
    fresh instance per retriever or per call, so `search` and the verifier's re-retrieval
    never hold duplicate GB-scale models in one process."""
    from groundly.retrieval.vector import VectorRetriever

    monkeypatch.setattr(embeddings_mod, "_shared", None)
    fake_instance = object()
    monkeypatch.setattr(embeddings_mod, "BgeM3Embedder", lambda: fake_instance)

    first = embeddings_mod.shared_embedder()
    second = embeddings_mod.shared_embedder()
    assert first is second is fake_instance

    store = SubjectStore(Path("/nonexistent/store.db"))
    retriever = VectorRetriever(store, rerank=False, top_k=1)
    assert retriever.embedder is fake_instance


class _Weights:
    """Stand-in weights; a class instance so a weakref can watch it die."""


class _CountingModel(embeddings_mod.LazyModel):
    """A LazyModel with stand-in weights, counting how often it builds."""

    def __init__(self) -> None:
        super().__init__()
        self.builds = 0

    def _build(self):
        self.builds += 1
        return _Weights()


def _wait_until_dead(ref: weakref.ref) -> None:
    deadline = time.monotonic() + 5
    while ref() is not None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ref() is None, "weights still alive after the idle window"


def test_lazy_model_drops_its_weights_after_the_idle_window_and_reloads_on_next_use():
    """A long-lived process (`groundly start`, a host's stdio server) gives the memory
    back once the model sits unused, and the next call loads it again transparently."""
    model = _CountingModel()
    model.idle_seconds = 0.05
    with model._use() as weights:
        ref = weakref.ref(weights)
    del weights
    _wait_until_dead(ref)

    with model._use():
        pass
    assert model.builds == 2
    model.unload()


def test_lazy_model_counts_the_idle_window_from_the_end_of_a_use():
    """A use longer than the window (a big index run) keeps its model, and the window
    starts when it ends: dropping it mid-use would free the weights into the device
    cache only after unload() had already emptied it, stranding GBs there."""
    model = _CountingModel()
    model.idle_seconds = 0.05
    with model._use() as weights:
        time.sleep(0.2)
        assert model._model is weights, "unloaded while in use"
        ref = weakref.ref(weights)
    del weights
    _wait_until_dead(ref)
    assert model.builds == 1


def test_lazy_model_keeps_its_weights_while_in_use():
    model = _CountingModel()
    with model._use() as first, model._use() as second:
        assert first is second
        model.unload()  # refused: in use
        assert model._model is first
    assert model.builds == 1
    model.unload()
    assert model._model is None
