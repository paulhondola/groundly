"""Contract tests for the verifier gate (P6 slice 1). These pin the machine-readable
contract both doors depend on: `verify_card` returns None iff the card passes, and
every failure is a `Rejection` whose reason comes from REJECTION_REASONS. Future
checks (answer key, distractors, code execution) extend `verify_card` behind the
same contract."""

from groundly.agents import verifier as verifier_mod
from groundly.agents.verifier import (
    REJECTION_REASONS,
    CardCandidate,
    Rejection,
    verify_card,
)
from groundly.core.manifest import EMBEDDING_DIM
from groundly.core.paths import subject_dir
from groundly.core.store import SubjectStore


class AlignedEmbedder:
    """Returns one fixed (dense, sparse) pair for every text — aim it at a specific
    chunk of the retrievable_subject fixture (chunk 1: dense [1,0,...], sparse {1:...};
    chunk 2: dense [0,1,...], sparse {3:...})."""

    def __init__(self, dense: list[float], sparse: dict[int, float]):
        self.dense = dense
        self.sparse = sparse

    def encode(self, texts):
        return [self.dense for _ in texts], [self.sparse for _ in texts]


def _chunk1_embedder():
    return AlignedEmbedder([1.0, 0.0] + [0.0] * (EMBEDDING_DIM - 2), {1: 0.9})


def _chunk2_embedder():
    return AlignedEmbedder([0.0, 1.0] + [0.0] * (EMBEDDING_DIM - 2), {3: 0.9})


def _store(subject_name: str) -> SubjectStore:
    return SubjectStore(subject_dir(subject_name) / "store.db")


def test_rejection_reasons_are_the_canonical_four():
    assert REJECTION_REASONS == (
        "not_answerable_from_chunks",
        "wrong_answer_key",
        "distractor_not_wrong",
        "reference_solution_failed",
    )


def test_verified_card_passes(retrievable_subject):
    card = CardCandidate(
        front="What does deadlock need?",
        back="mutual exclusion",
        chunk_ids=[1],
    )
    result = verify_card(card, _store(retrievable_subject), embedder=_chunk1_embedder())
    assert result is None


def test_empty_chunk_ids_rejected(retrievable_subject):
    card = CardCandidate(front="f", back="b", chunk_ids=[])
    result = verify_card(card, _store(retrievable_subject), embedder=_chunk1_embedder())
    assert isinstance(result, Rejection)
    assert result.reason == "not_answerable_from_chunks"
    assert result.reason in REJECTION_REASONS
    assert "no chunk_ids" in result.detail


def test_unresolvable_chunk_id_rejected_naming_the_ids(retrievable_subject):
    card = CardCandidate(front="f", back="b", chunk_ids=[1, 999])
    result = verify_card(card, _store(retrievable_subject), embedder=_chunk1_embedder())
    assert isinstance(result, Rejection)
    assert result.reason == "not_answerable_from_chunks"
    assert "999" in result.detail
    # the resolvable id is not blamed
    assert "[1," not in result.detail and "[1]" not in result.detail


def test_cited_chunk_outside_top_k_rejected(retrievable_subject, monkeypatch):
    # All 3 fixture chunks fit in the default top-20, so shrink the pool to 1 and
    # aim the query embedding at chunk 2 while citing chunk 1: the cited chunk
    # can't appear in the (single-slot) re-retrieval result.
    monkeypatch.setattr(verifier_mod, "VERIFY_TOP_K", 1)
    card = CardCandidate(
        front="semaphores and mutexes",
        back="synchronization",
        chunk_ids=[1],
    )
    result = verify_card(card, _store(retrievable_subject), embedder=_chunk2_embedder())
    assert isinstance(result, Rejection)
    assert result.reason == "not_answerable_from_chunks"
    assert "re-retriev" in result.detail


def test_no_search_trace_written_by_verification(retrievable_subject):
    # Verifier retrieval is internal machinery, not student activity — it must not
    # pollute the traces table (kind='search' rows are the search tool's).
    from groundly.core.progress import connect_progress

    card = CardCandidate(front="What does deadlock need?", back="mutual exclusion", chunk_ids=[1])
    verify_card(card, _store(retrievable_subject), embedder=_chunk1_embedder())

    conn = connect_progress(subject_dir(retrievable_subject) / "progress.db")
    try:
        count = conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0]
    finally:
        conn.close()
    assert count == 0
