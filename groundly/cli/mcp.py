"""`groundly mcp`: run the FastMCP tool surface over stdio for a host-spawned MCP
client (Claude Code/Codex/Desktop). P4 v1 — see cli/ask.py for the same lazy-import
wrapper pattern."""

import sys

import typer

from groundly.cli.app import app


@app.command()
def mcp() -> None:
    """Serve the groundly MCP tools (list_subjects/search/ask/get_page) over stdio."""
    from groundly.core.logs import setup_logging
    from groundly.mcp.server import mcp as mcp_server

    # No --debug flag here: the host spawns this process, so GROUNDLY_LOG_LEVEL is
    # the only reachable switch. A bad value is reported to stderr by hand rather
    # than through _fail() — that prints via the shared rich Console, i.e. stdout,
    # which is the MCP protocol stream.
    try:
        setup_logging()
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(code=1) from None
    mcp_server.run()
