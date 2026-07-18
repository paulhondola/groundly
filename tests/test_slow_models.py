"""Real-model checks — excluded by default (pyproject addopts); run: pytest -m slow.
First run downloads bge-m3 (~2.3 GB) to the HF cache."""

import pytest

pytestmark = pytest.mark.slow


def test_bge_m3_dense_and_sparse_contract():
    from groundly.core.manifest import EMBEDDING_DIM
    from groundly.llm.embeddings import BgeM3Embedder

    dense, sparse = BgeM3Embedder().encode(["mutual exclusion in distributed systems"])
    assert len(dense) == len(sparse) == 1
    assert len(dense[0]) == EMBEDDING_DIM
    norm_sq = sum(x * x for x in dense[0])
    assert abs(norm_sq - 1.0) < 1e-3  # manifest contract: normalized
    assert sparse[0], "learned sparse weights must be non-empty"
    assert all(isinstance(t, int) and w > 0 for t, w in sparse[0].items())


def test_bge_reranker_v2_m3_scores_relevant_pair_higher():
    from groundly.llm.rerank import BgeReranker

    scores = BgeReranker().compute_score(
        [
            ("what causes a deadlock?", "A deadlock needs mutual exclusion and circular wait."),
            ("what causes a deadlock?", "Semaphores coordinate producer-consumer queues."),
        ]
    )
    assert scores[0] > scores[1]


def test_cross_lingual_romanian_query_matches_english_deadlock_chunk(tmp_path, monkeypatch):
    """UC-02 criterion 2: a Romanian question over English-only slides retrieves the
    relevant chunk via the dense channel (docs/architecture/retrieval.md cross-lingual
    caveat — only the dense channel, not sparse/BM25, matches across languages)."""
    from groundly.core.paths import subject_dir
    from groundly.core.store import SQLiteSubjectStore
    from groundly.core.subject import init_subject
    from groundly.ingestion.extract import ChunkData
    from groundly.llm.embeddings import BgeM3Embedder
    from groundly.retrieval.vector import VectorRetriever

    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    init_subject("PDA")

    embedder = BgeM3Embedder()
    texts = [
        "A deadlock requires mutual exclusion, hold and wait, no preemption, and circular wait.",
        "Semaphores coordinate access between producer and consumer threads.",
    ]
    dense, sparse = embedder.encode(texts)
    chunks = [ChunkData(t, None, i + 1, 10) for i, t in enumerate(texts)]
    SQLiteSubjectStore(subject_dir("PDA") / "store.db").add_indexed(
        "slides.pdf", "a" * 64, 2, chunks, dense, sparse
    )

    store = SQLiteSubjectStore(subject_dir("PDA") / "store.db")
    retriever = VectorRetriever(store, embedder=embedder, rerank=False)
    nodes = retriever.retrieve("ce condiții sunt necesare pentru un deadlock?")
    assert nodes
    assert "mutual exclusion" in nodes[0].node.get_content()
