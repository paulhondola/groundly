"""`groundly mcp`: run the FastMCP tool surface over stdio for a host-spawned MCP
client (Claude Code/Codex/Desktop). A lazy-import wrapper: the server module loads only
when the verb runs."""

import sys

import typer

from groundly.cli.app import app


@app.command()
def mcp() -> None:
    """Serve the groundly MCP tools (list_subjects/search/get_page/submit_cards/
    list_decks/export_deck) over stdio."""
    from groundly.core.logs import setup_logging
    from groundly.mcp.server import mcp as mcp_server

    # No --debug flag: the host spawns this process, so GROUNDLY_LOG_LEVEL is the only
    # switch. A bad value goes to stderr by hand; _fail() prints to stdout, the MCP stream.
    try:
        setup_logging()
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(code=1) from None
    mcp_server.run()
