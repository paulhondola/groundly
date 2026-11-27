"""CLI: `groundly mcp` verb — thin stdio-runner wrapper (P4 v1)."""

import pytest
from typer.testing import CliRunner

from groundly.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _activity_registration_stays_in_the_test(monkeypatch):
    """The verbs register activity logging on the shared server and, for stdio, an exit hook;
    keep both from outliving the test (the hook would write to the real ~/.groundly)."""
    from groundly.mcp.server import mcp

    exits = []
    monkeypatch.setattr(mcp, "middleware", list(mcp.middleware))
    monkeypatch.setattr("atexit.register", lambda *a, **kw: exits.append(a))
    return exits


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


def _middleware_after(args, monkeypatch) -> list[str]:
    from fastmcp import FastMCP

    from groundly.mcp.server import mcp

    monkeypatch.setattr(FastMCP, "run", lambda self, **kw: None)
    assert runner.invoke(app, args).exit_code == 0
    return [type(m).__name__ for m in mcp.middleware]


def test_stdio_mcp_forwards_model_tools(monkeypatch):
    # Activity outside forwarding: the stdio server logs a forwarded call itself.
    names = _middleware_after(["mcp"], monkeypatch)
    assert names[-2:] == ["ActivityMiddleware", "ForwardMiddleware"]


def test_http_mcp_does_not_forward(monkeypatch):
    names = _middleware_after(["mcp", "--http"], monkeypatch)
    assert "ForwardMiddleware" not in names
    assert names[-1] == "ActivityMiddleware"


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


@pytest.mark.parametrize(("args", "source"), [([], "mcp_stdio"), (["--http"], "mcp_http")])
def test_mcp_verb_logs_activity(
    monkeypatch, _activity_registration_stays_in_the_test, args, source
):
    from fastmcp import FastMCP

    from groundly.mcp.activity import ActivityMiddleware
    from groundly.mcp.server import mcp

    monkeypatch.setattr(FastMCP, "run", lambda self, **kw: None)
    assert runner.invoke(app, ["mcp", *args]).exit_code == 0
    assert [m.source for m in mcp.middleware if isinstance(m, ActivityMiddleware)] == [source]
    exits = _activity_registration_stays_in_the_test
    assert exits == ([] if args else [(exits[0][0], "mcp_stdio", "session_end")])
