"""CLI: `groundly mcp` verb — thin stdio-runner wrapper (P4 v1)."""

from typer.testing import CliRunner

from groundly.cli import app

runner = CliRunner()


def test_mcp_verb_registered_and_runs_the_server(monkeypatch):
    # patch the class, not the module-level `mcp` instance: `run` is inherited from
    # fastmcp's TransportMixin, so monkeypatch's undo cannot remove an instance-level
    # patch — it writes the original back into `mcp.__dict__`, where it shadows the
    # *class* patch test_mcp_server.py's serve test relies on, and `groundly serve`
    # then boots a real HTTP server and blocks the whole suite forever.
    from fastmcp import FastMCP

    calls = []
    monkeypatch.setattr(FastMCP, "run", lambda self, **kw: calls.append(True))
    result = runner.invoke(app, ["mcp"])
    assert result.exit_code == 0, result.output
    assert calls == [True]
