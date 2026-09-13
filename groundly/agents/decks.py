"""Deck building through the one verifier gate (docs/architecture/agents.md): host-written
cards are verified and the ones that pass are stored. Rejected cards store nothing."""

from dataclasses import dataclass

from groundly.agents.verifier import CardCandidate, Rejection, verify_card
from groundly.core.store import SubjectStore
from groundly.core.subject import Subject

MAX_COUNT = 50  # cards per submit_cards call


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
    store = SubjectStore(Subject(subject).store_db_path)
    deck_id = store.get_or_create_deck(deck)

    outcomes: list[CardOutcome] = []
    for i, card in enumerate(cards):
        rejection = verify_card(card, store, embedder=embedder)
        if rejection is None:
            question_id = store.add_verified_card(
                deck_id, card.front, card.back, card.chunk_ids, generation_source
            )
            outcomes.append(CardOutcome(index=i, accepted=True, question_id=question_id))
        else:
            outcomes.append(CardOutcome(index=i, accepted=False, rejection=rejection))
    return outcomes
