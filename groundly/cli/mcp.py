"""`groundly mcp`: run the FastMCP tool surface over stdio for a host-spawned MCP
client (Claude Code/Codex/Desktop). P4 v1 — see cli/ask.py for the same lazy-import
wrapper pattern."""

from groundly.cli.app import app


@app.command()
def mcp() -> None:
    """Serve the groundly MCP tools (list_subjects/search/ask/get_page) over stdio."""
    from groundly.mcp.server import mcp as mcp_server

    mcp_server.run()
