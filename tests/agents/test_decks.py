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


# --- thick door: generate_deck_job ---------------------------------------------------

GOOD_CARD = '{"front": "What does deadlock need?", "back": "mutual exclusion", "chunk_ids": [1]}'
BAD_CARD = '{"front": "bogus", "back": "unsupported", "chunk_ids": [999]}'
FIXED_CARD = '{"front": "Deadlock requires?", "back": "mutual exclusion", "chunk_ids": [1]}'


def test_thick_loop_retries_rejected_cards_with_reason_fed_back(retrievable_subject, stub_chat):
    from groundly.agents.decks import generate_deck_job

    chat = stub_chat([f"[{GOOD_CARD}, {BAD_CARD}]", f"[{FIXED_CARD}]"])
    report = generate_deck_job(
        retrievable_subject,
        "deadlocks",
        "OS Deck",
        2,
        chat=chat,
        embedder=AlignedEmbedder(),
    )
    assert report["accepted"] == 2
    assert report["dropped"] == []
    assert report["requested"] == 2
    assert len(chat.calls) == 2
    # the retry prompt quotes the machine-readable rejection back to the model
    retry_text = str(chat.calls[1][1])
    assert "not_answerable_from_chunks" in retry_text
    assert chat.calls[1][0] == "generation"

    store = SQLiteSubjectStore(subject_dir(retrievable_subject) / "store.db")
    conn = store.connect()
    try:
        sources = {
            r["generation_source"] for r in conn.execute("SELECT generation_source FROM questions")
        }
    finally:
        conn.close()
    assert sources == {"server"}


def test_thick_loop_drops_after_max_retries(retrievable_subject, stub_chat):
    from groundly.agents.decks import generate_deck_job

    chat = stub_chat(f"[{BAD_CARD}]")  # same bad card every round
    report = generate_deck_job(
        retrievable_subject,
        "deadlocks",
        "OS Deck",
        1,
        chat=chat,
        embedder=AlignedEmbedder(),
    )
    assert report["accepted"] == 0
    assert len(report["dropped"]) == 1
    assert report["dropped"][0]["reason"] == "not_answerable_from_chunks"
    assert report["dropped"][0]["attempts"] == 3  # 1 + MAX_RETRIES rounds
    assert len(chat.calls) == 3


def test_thick_loop_unparseable_reply_burns_a_round_and_retries_same_prompt(
    retrievable_subject, stub_chat
):
    from groundly.agents.decks import generate_deck_job

    chat = stub_chat(["this is not json", f"[{GOOD_CARD}]"])
    report = generate_deck_job(
        retrievable_subject,
        "deadlocks",
        "OS Deck",
        1,
        chat=chat,
        embedder=AlignedEmbedder(),
    )
    assert report["accepted"] == 1
    assert len(chat.calls) == 2
    assert chat.calls[0][1] == chat.calls[1][1]  # identical messages retried


def test_thick_loop_all_rounds_unparseable_fails_with_named_cause(retrievable_subject, stub_chat):
    import pytest

    from groundly.agents.decks import generate_deck_job

    chat = stub_chat("never json")
    with pytest.raises(RuntimeError, match="unparseable output in all 3 rounds"):
        generate_deck_job(
            retrievable_subject,
            "deadlocks",
            "OS Deck",
            1,
            chat=chat,
            embedder=AlignedEmbedder(),
        )
    assert len(chat.calls) == 3


def test_thick_loop_tolerates_fenced_json(retrievable_subject, stub_chat):
    from groundly.agents.decks import generate_deck_job

    chat = stub_chat(f"```json\n[{GOOD_CARD}]\n```")
    report = generate_deck_job(
        retrievable_subject,
        "deadlocks",
        "OS Deck",
        1,
        chat=chat,
        embedder=AlignedEmbedder(),
    )
    assert report["accepted"] == 1


def test_thick_loop_empty_subject_fails_with_named_cause(subject, stub_chat):
    import pytest

    from groundly.agents.decks import generate_deck_job

    chat = stub_chat("[]")
    with pytest.raises(RuntimeError, match="no course material found"):
        generate_deck_job(subject, "anything", "D", 5, chat=chat, embedder=AlignedEmbedder())
    assert chat.calls == []  # failed before spending a single token


def test_thick_loop_records_one_trace_row(retrievable_subject, stub_chat):
    from groundly.agents.decks import generate_deck_job

    chat = stub_chat(f"[{GOOD_CARD}]")
    generate_deck_job(
        retrievable_subject,
        "deadlocks",
        "OS Deck",
        1,
        chat=chat,
        embedder=AlignedEmbedder(),
    )
    conn = connect_progress(subject_dir(retrievable_subject) / "progress.db")
    try:
        rows = conn.execute("SELECT kind, arm, outcome FROM traces").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert (rows[0]["kind"], rows[0]["arm"], rows[0]["outcome"]) == (
        "ask",
        "generate_deck",
        "answered",
    )


def test_estimate_generation_prices_from_config(monkeypatch, tmp_path):
    from groundly.agents.decks import estimate_generation

    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "config.toml").write_text(
        '[providers.generation]\nbase_url = "http://x"\nmodel = "m"\n'
        "input_price_per_mtok = 1.0\noutput_price_per_mtok = 2.0\n"
    )
    est = estimate_generation(20)
    assert est["estimated_tokens"] > 0
    assert est["estimated_cost_usd"] is not None and est["estimated_cost_usd"] > 0
    assert "confirm" in est["note"]


def test_estimate_generation_unpriced_provider_says_so(monkeypatch, tmp_path):
    from groundly.agents.decks import estimate_generation

    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    est = estimate_generation(20)
    assert est["estimated_cost_usd"] is None
    assert "no cost estimate" in est["note"]
