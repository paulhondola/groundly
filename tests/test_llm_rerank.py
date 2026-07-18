"""groundly/llm/rerank.py: lazy cross-encoder reranker. Fast tests only check
laziness (no model load on construction); real scoring is -m slow (test_slow_models.py)."""

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
