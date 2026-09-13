"""CLI: `groundly mcp` verb — thin stdio-runner wrapper (P4 v1)."""

from typer.testing import CliRunner

from groundly.cli import app

runner = CliRunner()


def test_mcp_verb_registered_and_runs_the_server(monkeypatch):
    # Patch the class, not the `mcp` instance: `run` comes from a fastmcp mixin, so undoing an
    # instance patch writes it into `mcp.__dict__`, shadowing the class patch the serve test
    # relies on, and `groundly serve` then boots a real server that blocks the suite forever.
    from fastmcp import FastMCP

    calls = []
    monkeypatch.setattr(FastMCP, "run", lambda self, **kw: calls.append(True))
    result = runner.invoke(app, ["mcp"])
    assert result.exit_code == 0, result.output
    assert calls == [True]
