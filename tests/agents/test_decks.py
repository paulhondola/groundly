"""The thin door (P6 slice 1): `submit_cards` is the single gate both doors call —
accepted cards land in store.db with their generation source, rejected cards store
nothing and come back with a machine-readable rejection, and every verdict lands in
progress.db's verifications table (the rejection-rate-by-source measurement)."""

from groundly.agents.decks import submit_cards
from groundly.agents.verifier import CardCandidate
from groundly.core.manifest import EMBEDDING_DIM
from groundly.core.paths import subject_dir
from groundly.core.store import SQLiteSubjectStore, connect_progress


class AlignedEmbedder:
    def encode(self, texts):
        return [[1.0, 0.0] + [0.0] * (EMBEDDING_DIM - 2) for _ in texts], [{1: 0.9} for _ in texts]


def _cards():
    return [
        CardCandidate(front="What does deadlock need?", back="mutual exclusion", chunk_ids=[1]),
        CardCandidate(front="bogus", back="cites nothing real", chunk_ids=[999]),
    ]


def test_mixed_batch_outcomes_in_order(retrievable_subject):
    outcomes = submit_cards(
        retrievable_subject,
        "OS Deck",
        _cards(),
        generation_source="host",
        embedder=AlignedEmbedder(),
    )
    assert [o.index for o in outcomes] == [0, 1]
    assert outcomes[0].accepted and outcomes[0].question_id is not None
    assert outcomes[0].rejection is None
    assert not outcomes[1].accepted and outcomes[1].question_id is None
    assert outcomes[1].rejection.reason == "not_answerable_from_chunks"


def test_accepted_stored_with_source_rejected_stores_nothing(retrievable_subject):
    submit_cards(
        retrievable_subject,
        "OS Deck",
        _cards(),
        generation_source="host",
        embedder=AlignedEmbedder(),
    )
    store = SQLiteSubjectStore(subject_dir(retrievable_subject) / "store.db")
    conn = store.connect()
    try:
        rows = conn.execute("SELECT body, generation_source FROM questions").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1  # the rejected card left no row behind
    assert rows[0]["body"] == "What does deadlock need?"
    assert rows[0]["generation_source"] == "host"

    cards = store.deck_cards("OS Deck")
    assert len(cards) == 1 and cards[0]["filename"] == "lec.pdf"


def test_every_verdict_recorded_in_verifications(retrievable_subject):
    submit_cards(
        retrievable_subject,
        "OS Deck",
        _cards(),
        generation_source="host",
        embedder=AlignedEmbedder(),
    )
    conn = connect_progress(subject_dir(retrievable_subject) / "progress.db")
    try:
        rows = conn.execute(
            "SELECT generation_source, reason FROM verifications ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 2
    assert rows[0]["generation_source"] == "host" and rows[0]["reason"] is None
    assert rows[1]["reason"] == "not_answerable_from_chunks"
