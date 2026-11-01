"""CLI: `groundly mcp` verb — thin stdio-runner wrapper (P4 v1)."""

from typer.testing import CliRunner

from groundly.cli import app

runner = CliRunner()


def test_mcp_verb_registered_and_runs_the_server(monkeypatch):
    # Patch the class, not the `mcp` instance: `run` comes from a fastmcp mixin, so undoing an
    # instance patch writes it into `mcp.__dict__`, shadowing the class patch the --http test
    # relies on, and `groundly mcp --http` then boots a real server that blocks the suite forever.
    from fastmcp import FastMCP

    calls = []
    monkeypatch.setattr(FastMCP, "run", lambda self, **kw: calls.append(True))
    result = runner.invoke(app, ["mcp"])
    assert result.exit_code == 0, result.output
    assert calls == [True]


def test_mcp_http_binds_loopback_with_origin_protection(monkeypatch):
    """`--http` passes the exact production kwargs to run(): loopback host and DNS-rebinding
    protection (tests/mcp/test_mcp_server.py exercises the ASGI app itself)."""
    from fastmcp import FastMCP

    calls: dict = {}
    monkeypatch.setattr(FastMCP, "run", lambda self, **kw: calls.update(kw))
    result = runner.invoke(app, ["mcp", "--http", "--port", "5150"])
    assert result.exit_code == 0, result.output
    assert calls == {
        "transport": "http",
        "host": "127.0.0.1",
        "port": 5150,
        "host_origin_protection": "auto",
    }


def test_mcp_port_without_http_is_an_error(monkeypatch):
    from fastmcp import FastMCP

    calls = []
    monkeypatch.setattr(FastMCP, "run", lambda self, **kw: calls.append(kw))
    result = runner.invoke(app, ["mcp", "--port", "5150"])
    assert result.exit_code == 1
    assert "--port requires --http" in result.stderr
    assert result.stdout == ""  # stdout is the stdio MCP stream
    assert calls == []


def test_mcp_debug_accepted_in_stdio_mode(monkeypatch):
    from fastmcp import FastMCP

    calls = []
    monkeypatch.setattr(FastMCP, "run", lambda self, **kw: calls.append(kw))
    result = runner.invoke(app, ["mcp", "--debug"])
    assert result.exit_code == 0, result.output
    assert calls == [{}]
