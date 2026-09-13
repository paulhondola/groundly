"""`groundly export-graph`: the CLI wrapper over core/graph_html.py's export_graph_html."""

from typer.testing import CliRunner

from groundly.cli import app

runner = CliRunner()


def test_export_graph_no_graph_fails_with_named_cause(subject):
    """The command must route GraphHtmlError through `_fail` — a named cause printed to
    the terminal and exit code 1, never a bare traceback."""
    result = runner.invoke(app, ["export-graph", subject])
    assert result.exit_code == 1
    assert "Error:" in result.output  # _fail's format — proves a named cause was printed


def test_export_graph_uninitialized_subject_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    result = runner.invoke(app, ["export-graph", "NOPE"])
    assert result.exit_code == 1
    assert "not initialized" in result.output
