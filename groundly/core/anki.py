"""Verified decks -> Anki .apkg via genanki (P6 slice 1; decision 6: Anki owns daily
review, Groundly owns verified generation). Citations render on the card back — the
UC-11 acceptance criterion. Ids are deterministic (sha-derived deck id, guid_for note
guids) so a re-export updates the deck in Anki instead of duplicating it."""

import hashlib
from pathlib import Path

from groundly.core.store import SubjectStore, check_deck_name
from groundly.core.subject import Subject

_MODEL_ID = 1607392319  # random-once constant, genanki's documented convention

_BACK_TEMPLATE = "{{Back}}<hr id=sources><div class=sources>{{Sources}}</div>"


def _deck_id(subject: str, deck: str) -> int:
    digest = hashlib.sha256(f"groundly/{subject}/{deck}".encode()).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF  # genanki wants 31-bit-safe


def _source_line(row) -> str:
    page = f", p.{row['page']}" if row["page"] is not None else ""
    heading = f" — {row['heading_path']}" if row["heading_path"] else ""
    return f"{row['filename']}{page}{heading}"


def export_deck(subject_name: str, deck_name: str, out_path: Path | None = None) -> Path:
    """Write `deck_name` as an .apkg. Default target: <subject>/exports/<deck>.apkg —
    outside the bundle allowlist, so exported decks never leak into .groundly bundles."""
    import genanki  # lazy: only the export path pays the import

    check_deck_name(deck_name)  # before any path is built — imported stores are untrusted
    subj = Subject(subject_name)
    store = SubjectStore(subj.store_db_path)
    rows = store.deck_cards(deck_name)
    if not rows:
        raise ValueError(f"deck {deck_name!r} has no cards — list_decks shows what exists")

    model = genanki.Model(
        _MODEL_ID,
        "Groundly Card",
        fields=[{"name": "Front"}, {"name": "Back"}, {"name": "Sources"}],
        templates=[{"name": "Card", "qfmt": "{{Front}}", "afmt": _BACK_TEMPLATE}],
    )
    deck = genanki.Deck(_deck_id(subject_name, deck_name), deck_name)

    # deck_cards is ordered by (question_id, chunk_id): one note per question,
    # one source line per citation row
    cards: dict[int, dict] = {}
    for row in rows:
        card = cards.setdefault(
            row["question_id"], {"front": row["body"], "back": row["answer"], "sources": []}
        )
        card["sources"].append(_source_line(row))

    for question_id, card in cards.items():
        deck.add_note(
            genanki.Note(
                model=model,
                fields=[card["front"], card["back"], "<br>".join(card["sources"])],
                guid=genanki.guid_for(subject_name, deck_name, question_id),
            )
        )

    if out_path is None:
        out_path = subj.root_dir / "exports" / f"{deck_name}.apkg"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    genanki.Package(deck).write_to_file(out_path)
    return out_path
