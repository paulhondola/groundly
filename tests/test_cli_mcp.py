"""CLI: `groundly mcp` verb — thin stdio-runner wrapper (P4 v1)."""

from typer.testing import CliRunner

from groundly.cli import app

runner = CliRunner()


def test_mcp_verb_registered_and_runs_the_server(monkeypatch):
    calls = []
    monkeypatch.setattr("groundly.mcp.server.mcp.run", lambda: calls.append(True))
    result = runner.invoke(app, ["mcp"])
    assert result.exit_code == 0, result.output
    assert calls == [True]
