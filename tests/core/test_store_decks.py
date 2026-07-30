"""store.db v2: decks/questions/question_citations, the v1 -> v2 migration, and the
SubjectStore deck/card methods (P6 slice 1 design doc)."""

import sqlite3

import pytest
import sqlite_vec

from groundly.core import store
from groundly.core.manifest import EMBEDDING_DIM
from groundly.core.store import SubjectStore


def _add_material_with_chunk(conn, filename="lec.pdf", sha="a" * 64):
    with conn:
        mid = conn.execute(
            "INSERT INTO materials (filename, sha256, status, pages) VALUES (?, ?, 'indexed', 1)",
            (filename, sha),
        ).lastrowid
        cid = conn.execute(
            "INSERT INTO chunks (material_id, page, heading_path, text, token_count) "
            "VALUES (?, 1, 'Intro', 'deadlock needs mutual exclusion', 10)",
            (mid,),
        ).lastrowid
        conn.execute(
            "INSERT INTO vectors (rowid, embedding) VALUES (?, ?)",
            (cid, sqlite_vec.serialize_float32([0.1] * EMBEDDING_DIM)),
        )
    return mid, cid


# --- migration --------------------------------------------------------------------


def test_fresh_create_store_lands_at_v2(tmp_path):
    path = tmp_path / "store.db"
    store.create_store(path)
    conn = store.connect(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        # tables exist and are queryable
        assert conn.execute("SELECT COUNT(*) FROM decks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM question_citations").fetchone()[0] == 0
    finally:
        conn.close()


def test_v1_store_upgrades_to_v2_on_open(tmp_path):
    path = tmp_path / "store.db"
    conn = store.connect(path, create=True)
    conn.executescript(store._SCHEMA)
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()

    conn = store.connect(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM decks").fetchone()[0] == 0
    finally:
        conn.close()


# --- add_verified_card --------------------------------------------------------------


def test_add_verified_card_stores_question_and_citations(tmp_path):
    path = tmp_path / "store.db"
    store.create_store(path)
    conn = store.connect(path)
    _mid, cid = _add_material_with_chunk(conn)
    conn.close()

    store_obj = SubjectStore(path)
    deck_id = store_obj.get_or_create_deck("Midterm")
    qid = store_obj.add_verified_card(deck_id, "front?", "back.", [cid], "host")

    conn = store.connect(path)
    try:
        row = conn.execute("SELECT * FROM questions WHERE id = ?", (qid,)).fetchone()
        assert row["body"] == "front?"
        assert row["answer"] == "back."
        assert row["type"] == "flashcard"
        assert row["generation_source"] == "host"
        cites = conn.execute(
            "SELECT chunk_id FROM question_citations WHERE question_id = ?", (qid,)
        ).fetchall()
        assert [r["chunk_id"] for r in cites] == [cid]
    finally:
        conn.close()


def test_add_verified_card_bogus_chunk_id_rolls_back_everything(tmp_path):
    path = tmp_path / "store.db"
    store.create_store(path)
    store_obj = SubjectStore(path)
    deck_id = store_obj.get_or_create_deck("Midterm")

    with pytest.raises(sqlite3.IntegrityError):
        store_obj.add_verified_card(deck_id, "front?", "back.", [999999], "host")

    conn = store.connect(path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM question_citations").fetchone()[0] == 0
    finally:
        conn.close()


def test_get_or_create_deck_is_idempotent(tmp_path):
    path = tmp_path / "store.db"
    store.create_store(path)
    store_obj = SubjectStore(path)
    id1 = store_obj.get_or_create_deck("Midterm")
    id2 = store_obj.get_or_create_deck("Midterm")
    assert id1 == id2


# --- remove_material orphan cleanup --------------------------------------------------


def test_remove_material_deletes_cards_stripped_of_all_citations(tmp_path):
    path = tmp_path / "store.db"
    store.create_store(path)
    conn = store.connect(path)
    mid, cid = _add_material_with_chunk(conn)
    conn.close()

    store_obj = SubjectStore(path)
    deck_id = store_obj.get_or_create_deck("Midterm")
    qid = store_obj.add_verified_card(deck_id, "front?", "back.", [cid], "host")

    store_obj.remove_material(mid)

    conn = store.connect(path)
    try:
        assert (
            conn.execute("SELECT COUNT(*) FROM questions WHERE id = ?", (qid,)).fetchone()[0] == 0
        )
    finally:
        conn.close()


def test_remove_material_keeps_cards_still_cited_elsewhere(tmp_path):
    path = tmp_path / "store.db"
    store.create_store(path)
    conn = store.connect(path)
    mid1, cid1 = _add_material_with_chunk(conn, "lec1.pdf", "a" * 64)
    mid2, cid2 = _add_material_with_chunk(conn, "lec2.pdf", "b" * 64)
    conn.close()

    store_obj = SubjectStore(path)
    deck_id = store_obj.get_or_create_deck("Midterm")
    qid = store_obj.add_verified_card(deck_id, "front?", "back.", [cid1, cid2], "host")

    store_obj.remove_material(mid1)  # cid1's material gone, cid2 still cites qid

    conn = store.connect(path)
    try:
        assert (
            conn.execute("SELECT COUNT(*) FROM questions WHERE id = ?", (qid,)).fetchone()[0] == 1
        )
    finally:
        conn.close()


# --- deck_cards / list_decks ---------------------------------------------------------


def test_deck_cards_shape_and_order(tmp_path):
    path = tmp_path / "store.db"
    store.create_store(path)
    conn = store.connect(path)
    _mid, cid = _add_material_with_chunk(conn)
    conn.close()

    store_obj = SubjectStore(path)
    deck_id = store_obj.get_or_create_deck("Midterm")
    qid = store_obj.add_verified_card(deck_id, "front?", "back.", [cid], "host")

    rows = store_obj.deck_cards("Midterm")
    assert len(rows) == 1
    row = rows[0]
    assert row["question_id"] == qid
    assert row["body"] == "front?"
    assert row["answer"] == "back."
    assert row["chunk_id"] == cid
    assert row["filename"] == "lec.pdf"
    assert row["page"] == 1
    assert row["heading_path"] == "Intro"


def test_deck_cards_unknown_deck_returns_empty(tmp_path):
    path = tmp_path / "store.db"
    store.create_store(path)
    store_obj = SubjectStore(path)
    assert store_obj.deck_cards("Nope") == []


def test_list_decks_reports_name_and_card_count(tmp_path):
    path = tmp_path / "store.db"
    store.create_store(path)
    conn = store.connect(path)
    _mid, cid = _add_material_with_chunk(conn)
    conn.close()

    store_obj = SubjectStore(path)
    deck_id = store_obj.get_or_create_deck("Midterm")
    store_obj.add_verified_card(deck_id, "f1", "b1", [cid], "host")
    store_obj.add_verified_card(deck_id, "f2", "b2", [cid], "host")
    store_obj.get_or_create_deck("Empty")

    rows = {r["name"]: r["card_count"] for r in store_obj.list_decks()}
    assert rows == {"Midterm": 2, "Empty": 0}
