"""Deck building: the two doors through the one verifier gate (P6 slice 1 design
doc, docs/architecture/agents.md §2). `submit_cards` is that gate made literal —
the thin door (MCP `submit_cards`, host-generated, zero-key) and the thick door
(`generate_deck`'s loop) both call it; nothing else writes cards. Rejected cards
store nothing; every verdict is recorded in progress.db for the
rejection-rate-by-source measurement."""

from dataclasses import dataclass

from groundly.agents.verifier import CardCandidate, Rejection, verify_card
from groundly.core.store import (
    SQLiteSubjectStore,
    connect_progress,
    record_verification,
)
from groundly.core.subject import Subject


@dataclass
class CardOutcome:
    index: int
    accepted: bool
    question_id: int | None = None
    rejection: Rejection | None = None


def submit_cards(
    subject: str,
    deck: str,
    cards: list[CardCandidate],
    *,
    generation_source: str,
    embedder=None,
) -> list[CardOutcome]:
    """Verify every card and store the ones that pass into `deck`. Zero-key: the
    verifier touches only local bge-m3 (lazily), never a provider."""
    subj = Subject(subject)
    store = SQLiteSubjectStore(subj.store_db_path)
    deck_id = store.get_or_create_deck(deck)
    progress_conn = connect_progress(subj.progress_db_path)

    outcomes: list[CardOutcome] = []
    try:
        for i, card in enumerate(cards):
            rejection = verify_card(card, store, embedder=embedder)
            if rejection is None:
                question_id = store.add_verified_card(
                    deck_id, card.front, card.back, card.chunk_ids, generation_source
                )
                outcomes.append(CardOutcome(index=i, accepted=True, question_id=question_id))
            else:
                outcomes.append(CardOutcome(index=i, accepted=False, rejection=rejection))
            record_verification(
                progress_conn,
                generation_source=generation_source,
                reason=None if rejection is None else rejection.reason,
            )
    finally:
        progress_conn.close()
    return outcomes
