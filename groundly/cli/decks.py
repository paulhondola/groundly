"""Deck export verb: `groundly export-deck SUBJECT DECK [--out PATH]` — the batch
half of UC-11 (building decks is host-conversational, writing the .apkg is batch)."""

from pathlib import Path
from typing import Annotated, Optional

import typer

from groundly.cli.app import _fail, _subject_checked, app, console


@app.command("export-deck")
def export_deck(
    subject: Annotated[str, typer.Argument(help="Subject the deck belongs to.")],
    deck: Annotated[str, typer.Argument(help="Deck name (see list_decks via MCP).")],
    out: Annotated[
        Optional[Path],
        typer.Option("--out", "-o", help="Output .apkg path (default: ./<DECK>.apkg)."),
    ] = None,
) -> None:
    """Export a verified flashcard deck as an Anki .apkg (citations on card backs)."""
    from groundly.core.anki import export_deck as export_deck_fn

    _subject_checked(subject)
    target = out if out is not None else Path.cwd() / f"{deck}.apkg"
    try:
        written = export_deck_fn(subject, deck, target)
    except ValueError as exc:
        _fail(str(exc))
    console.print(f"Exported [bold]{deck}[/bold] to {written}")
