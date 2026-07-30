"""`groundly export-deck`: the CLI wrapper over core/anki.py's export_deck."""

from typer.testing import CliRunner

from groundly.cli import app
from groundly.core.paths import subject_dir
from groundly.core.store import SubjectStore

runner = CliRunner()


def _seed_deck(subject_name: str) -> None:
    store = SubjectStore(subject_dir(subject_name) / "store.db")
    deck_id = store.get_or_create_deck("OS Deck")
    store.add_verified_card(deck_id, "front", "back", [1], "host")


def test_export_deck_writes_apkg(retrievable_subject, tmp_path):
    _seed_deck(retrievable_subject)
    out = tmp_path / "os.apkg"
    result = runner.invoke(app, ["export-deck", "TEST", "OS Deck", "--out", str(out)])
    assert result.exit_code == 0
    assert out.exists()
    assert "Exported" in result.output


def test_export_deck_unknown_deck_fails_with_named_cause(retrievable_subject):
    result = runner.invoke(app, ["export-deck", "TEST", "Nope"])
    assert result.exit_code == 1
    assert "has no cards" in result.output


def test_export_deck_uninitialized_subject_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    result = runner.invoke(app, ["export-deck", "NOPE", "D"])
    assert result.exit_code == 1
    assert "not initialized" in result.output
