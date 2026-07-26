"""`.apkg` export (UC-11's acceptance criterion): the deck imports into stock Anki
with cards, answers, and source citations on the back. An .apkg is a zip holding
`collection.anki2` (SQLite) — stdlib zipfile+sqlite3 validate the real artifact,
no Anki needed. Deterministic ids make re-export update-in-place, not duplicate."""

import sqlite3
import zipfile

import pytest

from groundly.core.anki import export_deck
from groundly.core.paths import subject_dir
from groundly.core.store import SQLiteSubjectStore


def _seed_deck(subject_name: str) -> None:
    store = SQLiteSubjectStore(subject_dir(subject_name) / "store.db")
    deck_id = store.get_or_create_deck("OS Deck")
    store.add_verified_card(deck_id, "What does deadlock need?", "mutual exclusion", [1, 3], "host")
    store.add_verified_card(deck_id, "What synchronizes threads?", "semaphores", [2], "host")


def _note_fields(apkg_path) -> list[str]:
    with zipfile.ZipFile(apkg_path) as zf:
        with zf.open("collection.anki2") as member, open(apkg_path.parent / "c.anki2", "wb") as out:
            out.write(member.read())
    conn = sqlite3.connect(apkg_path.parent / "c.anki2")
    try:
        return [r[0] for r in conn.execute("SELECT flds FROM notes ORDER BY id")]
    finally:
        conn.close()


def test_export_writes_apkg_with_fronts_backs_and_citations(retrievable_subject, tmp_path):
    _seed_deck(retrievable_subject)
    out = export_deck(retrievable_subject, "OS Deck", tmp_path / "os.apkg")
    assert out == tmp_path / "os.apkg" and out.exists()

    fields = _note_fields(out)
    assert len(fields) == 2
    joined = "\n".join(fields)
    assert "What does deadlock need?" in joined
    assert "mutual exclusion" in joined
    assert "lec.pdf" in joined  # citation source on the card
    assert "p.1" in joined and "p.3" in joined  # both cited pages of card 1
    assert "Intro > Deadlocks" in joined  # heading path


def test_export_default_path_is_subject_exports_dir(retrievable_subject):
    _seed_deck(retrievable_subject)
    out = export_deck(retrievable_subject, "OS Deck")
    assert out == subject_dir(retrievable_subject) / "exports" / "OS Deck.apkg"
    assert out.exists()


def test_reexport_is_deterministic(retrievable_subject, tmp_path):
    _seed_deck(retrievable_subject)
    a = export_deck(retrievable_subject, "OS Deck", tmp_path / "a.apkg")
    b = export_deck(retrievable_subject, "OS Deck", tmp_path / "b.apkg")

    def _identity(path):
        with zipfile.ZipFile(path) as zf:
            with zf.open("collection.anki2") as member:
                raw = member.read()
        tmp = path.parent / (path.stem + ".anki2")
        tmp.write_bytes(raw)
        conn = sqlite3.connect(tmp)
        try:
            notes = conn.execute("SELECT guid FROM notes ORDER BY guid").fetchall()
            decks_col = conn.execute("SELECT decks FROM col").fetchone()[0]
        finally:
            conn.close()
        return notes, decks_col

    notes_a, decks_a = _identity(a)
    notes_b, decks_b = _identity(b)
    assert notes_a == notes_b  # stable guids: Anki updates in place, never duplicates
    assert decks_a == decks_b  # same sha-derived deck id


def test_unknown_or_empty_deck_fails_with_named_cause(retrievable_subject):
    with pytest.raises(ValueError, match="has no cards — list_decks shows what exists"):
        export_deck(retrievable_subject, "Nope")


@pytest.mark.parametrize(
    "hostile",
    ["../escape", "..\\escape", "/tmp/abs", "a/b", "..", "", "   "],
)
def test_hostile_deck_names_rejected_before_any_path_use(retrievable_subject, hostile):
    # Deck names are the one host-controlled string that reaches the filesystem
    # (exports/<deck>.apkg). Rejected at export even if a row exists (imported
    # store.db is untrusted — a hostile bundle can carry any decks row).
    with pytest.raises(ValueError, match="invalid deck name"):
        export_deck(retrievable_subject, hostile)


@pytest.mark.parametrize("hostile", ["../escape", "/tmp/abs", "a/b", ""])
def test_hostile_deck_names_rejected_at_creation(retrievable_subject, hostile):
    store = SQLiteSubjectStore(subject_dir(retrievable_subject) / "store.db")
    with pytest.raises(ValueError, match="invalid deck name"):
        store.get_or_create_deck(hostile)
