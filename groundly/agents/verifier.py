"""The verifier gate (P6 slice 1 design doc): the single check both the thin
(`submit_cards`) and thick (`generate_deck`) doors run before anything lands in
store.db. Nothing unverified enters the question bank
(.claude/rules/grounding-and-privacy.md).

This slice implements the first two of the four canonical rejection reasons —
citation resolution and answerability-by-re-retrieval. Answer-key / distractor /
code-execution checks arrive with UC-10/13, added as further checks inside
`verify_card` without changing its signature or the `Rejection` contract.
"""

from dataclasses import dataclass

from groundly.core.store import SQLiteSubjectStore
from groundly.retrieval.vector import VectorRetriever

REJECTION_REASONS = (
    "not_answerable_from_chunks",
    "wrong_answer_key",
    "distractor_not_wrong",
    "reference_solution_failed",
)

VERIFY_TOP_K = 20  # re-retrieval pool checked for membership of a cited chunk id


@dataclass
class CardCandidate:
    front: str
    back: str
    chunk_ids: list[int]


@dataclass
class Rejection:
    reason: str  # one of REJECTION_REASONS — the machine-readable contract
    detail: str  # specific cause, host/human-readable


def verify_card(
    card: CardCandidate, store: SQLiteSubjectStore, *, embedder=None
) -> Rejection | None:
    """Fail-fast, cheapest check first. Returns None iff the card passes every
    check implemented so far."""
    if not card.chunk_ids:
        return Rejection(
            "not_answerable_from_chunks",
            "no chunk_ids given — cite the chunk_id of every supporting chunk returned by search",
        )

    resolved = {row["chunk_id"] for row in store.chunk_details(card.chunk_ids)}
    missing = [cid for cid in card.chunk_ids if cid not in resolved]
    if missing:
        return Rejection(
            "not_answerable_from_chunks",
            f"chunk_ids {missing} do not resolve to any chunk in this subject — "
            "cite only chunk_id values returned by search",
        )

    retriever = VectorRetriever(store, embedder=embedder, rerank=False, context_k=VERIFY_TOP_K)
    nodes = retriever.retrieve(card.front + "\n" + card.back)
    retrieved_ids = {n.node.metadata["chunk_id"] for n in nodes}
    if not (set(card.chunk_ids) & retrieved_ids):
        return Rejection(
            "not_answerable_from_chunks",
            f"re-retrieving the front+back text did not surface any cited chunk in the "
            f"top {VERIFY_TOP_K} results — the card doesn't look answerable from its own citations",
        )

    return None
