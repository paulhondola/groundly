"""`groundly serve`: run the FastMCP tool surface over Streamable HTTP for hosts
that connect via URL instead of spawning a stdio subprocess. Same lazy-import
wrapper pattern as cli/mcp.py."""

import typer

from groundly.cli.app import app


@app.command()
def serve(
    port: int = typer.Option(8000, help="Port to bind on 127.0.0.1."),
) -> None:
    """Serve the groundly MCP tools over Streamable HTTP on 127.0.0.1."""
    # ponytail: loopback only, no --host flag — the security.md non-loopback
    # override flag is the documented upgrade path if remote access is ever needed.
    from groundly.mcp.server import mcp as mcp_server

    # host_origin_protection rejects DNS-rebinding requests (hostile Host/Origin
    # headers) — loopback binding alone doesn't; fastmcp defaults it to off.
    mcp_server.run(transport="http", host="127.0.0.1", port=port, host_origin_protection="auto")
