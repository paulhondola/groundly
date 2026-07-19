"""groundly/retrieval/vector.py: rrf() math, VectorRetriever fusion/rerank, search()."""

import pytest

from groundly.core.manifest import EMBEDDING_DIM
from groundly.core.paths import subject_dir
from groundly.core.store import SQLiteSubjectStore, connect
from groundly.retrieval.vector import CONTEXT_K, VectorRetriever, rrf, search


class QueryEmbedder:
    """Returns a fixed dense/sparse pair for any query text (one encode() call)."""

    def __init__(self, dense, sparse):
        self.dense = dense
        self.sparse = sparse
        self.calls = 0

    def encode(self, texts):
        self.calls += 1
        return [self.dense for _ in texts], [self.sparse for _ in texts]


class StubReranker:
    def __init__(self, scores=None, fail=False):
        self.scores = scores
        self.fail = fail
        self.pairs = None

    def compute_score(self, pairs):
        if self.fail:
            raise AssertionError("reranker must not be called when rerank=False")
        self.pairs = pairs
        return self.scores if self.scores is not None else [0.0] * len(pairs)


def _near_embedder():
    return QueryEmbedder([1.0, 0.0] + [0.0] * (EMBEDDING_DIM - 2), {1: 1.0})


# --- rrf() pure math ----------------------------------------------------------------


def test_rrf_fuses_multiple_rankings_by_reciprocal_rank():
    fused = rrf([[1, 2, 3], [2, 1, 3]], k=60)
    ids = [doc_id for doc_id, _ in fused]
    assert ids[0] in (1, 2)  # 1 and 2 both appear at rank 0/1 across lists — tied for top
    assert set(ids) == {1, 2, 3}
    # exact score check for id 1: rank 0 in list A (1/61) + rank 1 in list B (1/62)
    scores = dict(fused)
    assert scores[1] == pytest.approx(1 / 61 + 1 / 62)
    assert scores[3] == pytest.approx(1 / 63 + 1 / 63)


def test_rrf_empty_rankings_returns_empty():
    assert rrf([]) == []
    assert rrf([[], []]) == []


def test_rrf_single_ranking_preserves_order():
    fused = rrf([[5, 1, 9]])
    assert [doc_id for doc_id, _ in fused] == [5, 1, 9]


# --- VectorRetriever ------------------------------------------------------------------


def test_vector_retriever_fuses_channels_and_ranks_relevant_chunk_first(retrievable_subject):
    store_obj = SQLiteSubjectStore(subject_dir(retrievable_subject) / "store.db")
    retriever = VectorRetriever(store_obj, embedder=_near_embedder(), rerank=False)
    nodes = retriever.retrieve("deadlock")
    ids = [n.node.metadata["chunk_id"] for n in nodes]
    assert ids[0] == 1  # dense=near, sparse=token 1 both point at chunk 1; bm25 too
    assert 2 not in ids[:2]  # chunk 2 ("semaphores") is off-topic on every channel


def test_vector_retriever_node_metadata_and_text(retrievable_subject):
    store_obj = SQLiteSubjectStore(subject_dir(retrievable_subject) / "store.db")
    retriever = VectorRetriever(store_obj, embedder=_near_embedder(), rerank=False)
    nodes = retriever.retrieve("deadlock")
    node = next(n for n in nodes if n.node.metadata["chunk_id"] == 1)
    assert node.node.metadata["filename"] == "lec.pdf"
    assert node.node.metadata["page"] == 1
    assert node.node.metadata["heading_path"] == "Intro > Deadlocks"
    assert "mutual exclusion" in node.node.get_content()


def test_vector_retriever_path_without_rerank(retrievable_subject):
    store_obj = SQLiteSubjectStore(subject_dir(retrievable_subject) / "store.db")
    retriever = VectorRetriever(store_obj, embedder=_near_embedder(), rerank=False)
    retriever.retrieve("deadlock")
    assert retriever.path == ["dense", "sparse", "bm25", "rrf"]


def test_vector_retriever_reranker_skipped_when_rerank_false(retrievable_subject):
    store_obj = SQLiteSubjectStore(subject_dir(retrievable_subject) / "store.db")
    reranker = StubReranker(fail=True)
    retriever = VectorRetriever(
        store_obj, embedder=_near_embedder(), reranker=reranker, rerank=False
    )
    retriever.retrieve("deadlock")  # must not raise — reranker.compute_score never called


def test_vector_retriever_reranks_when_enabled(retrievable_subject):
    store_obj = SQLiteSubjectStore(subject_dir(retrievable_subject) / "store.db")
    # invert the fused order: chunk 2 ("semaphores") scores highest under the reranker
    reranker = StubReranker(scores=None)

    def scored(pairs):
        reranker.pairs = pairs
        # pairs are (query, chunk_text); score chunk 2's text highest
        return [1.0 if "semaphores" in text else 0.0 for _, text in pairs]

    reranker.compute_score = scored
    retriever = VectorRetriever(
        store_obj, embedder=_near_embedder(), reranker=reranker, rerank=True
    )
    nodes = retriever.retrieve("deadlock")
    assert nodes[0].node.metadata["chunk_id"] == 2
    assert retriever.path == ["dense", "sparse", "bm25", "rrf", "rerank"]


def test_vector_retriever_empty_store_returns_no_nodes(subject):
    store_obj = SQLiteSubjectStore(subject_dir(subject) / "store.db")
    retriever = VectorRetriever(store_obj, embedder=_near_embedder(), rerank=False)
    assert retriever.retrieve("anything") == []


def test_vector_retriever_respects_context_k(retrievable_subject):
    store_obj = SQLiteSubjectStore(subject_dir(retrievable_subject) / "store.db")
    retriever = VectorRetriever(store_obj, embedder=_near_embedder(), rerank=False, context_k=1)
    nodes = retriever.retrieve("deadlock")
    assert len(nodes) == 1


# --- search() shared function ---------------------------------------------------------


def test_search_returns_nodes_and_records_trace(retrievable_subject):
    nodes = search(retrievable_subject, "deadlock", embedder=_near_embedder(), rerank=False)
    assert len(nodes) <= CONTEXT_K
    assert nodes
    conn = connect(subject_dir(retrievable_subject) / "store.db")
    conn.close()
    from groundly.core.store import connect_progress

    pconn = connect_progress(subject_dir(retrievable_subject) / "progress.db")
    try:
        row = pconn.execute("SELECT * FROM traces").fetchone()
        assert row["kind"] == "search"
        assert row["outcome"] == "results"
        assert row["arm"] == "vector"
        assert row["query"] == "deadlock"
        import json

        assert json.loads(row["path"]) == ["dense", "sparse", "bm25", "rrf"]
        assert json.loads(row["chunk_ids"])
        assert row["latency_ms"] is not None
    finally:
        pconn.close()
