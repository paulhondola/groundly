"""`groundly mcp`: run the FastMCP tool surface over stdio for a host-spawned MCP client
(Claude Code/Codex/Desktop), or over Streamable HTTP on loopback with `--http` for hosts
that connect by URL. A lazy-import wrapper: the server module loads only when the verb
runs."""

import sys
from typing import Annotated

import typer

from groundly.cli.app import app


def _stderr_fail(message: str) -> None:
    # stdout is the stdio MCP stream; _fail() prints there, so errors go to stderr by hand
    print(f"Error: {message}", file=sys.stderr)
    raise typer.Exit(code=1)


@app.command()
def mcp(
    http: Annotated[
        bool, typer.Option("--http", help="Serve Streamable HTTP on 127.0.0.1 instead of stdio.")
    ] = False,
    port: Annotated[
        int | None, typer.Option("--port", help="Port to bind with --http (default 8000).")
    ] = None,
    debug: Annotated[
        bool,
        typer.Option(
            "--debug", help="Stream debug logs to stderr (also: GROUNDLY_LOG_LEVEL=DEBUG)."
        ),
    ] = False,
) -> None:
    """Serve the groundly MCP tools over stdio, or Streamable HTTP on 127.0.0.1 with --http."""
    from groundly.core.logs import setup_logging
    from groundly.mcp.server import mcp as mcp_server

    if port is not None and not http:
        _stderr_fail("--port requires --http")
    try:
        setup_logging(debug)
    except ValueError as exc:
        _stderr_fail(str(exc))

    if not http:
        mcp_server.run()
        return
    # ponytail: loopback only, no --host flag — the security.md non-loopback
    # override flag is the documented upgrade path if remote access is ever needed.
    # host_origin_protection rejects DNS-rebinding requests (hostile Host/Origin
    # headers) — loopback binding alone doesn't; fastmcp defaults it to off.
    mcp_server.run(
        transport="http",
        host="127.0.0.1",
        port=port if port is not None else 8000,
        host_origin_protection="auto",
    )
