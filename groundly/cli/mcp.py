"""`groundly mcp`: run the FastMCP tool surface over stdio for a host-spawned MCP client
(Claude Code/Codex/Desktop), or over Streamable HTTP on loopback with `--http` for hosts
that connect by URL. A lazy-import wrapper: the server module loads only when the verb
runs."""

import atexit
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
    page_map_subject: Annotated[str | None, typer.Option(hidden=True)] = None,
) -> None:
    """Serve the groundly MCP tools over stdio, or Streamable HTTP on 127.0.0.1 with --http."""
    from groundly.core import activity
    from groundly.core.logs import setup_logging
    from groundly.mcp.activity import ActivityMiddleware
    from groundly.mcp.server import mcp as public_server

    if port is not None and not http:
        _stderr_fail("--port requires --http")
    mcp_server = public_server
    if page_map_subject is not None:
        from groundly.core.subject import Subject
        from groundly.mcp.page_map import create_page_map_server

        if http:
            _stderr_fail("a page map requires stdio")
        try:
            Subject(page_map_subject)
        except ValueError as exc:
            _stderr_fail(str(exc))
        mcp_server = create_page_map_server(page_map_subject)
    try:
        setup_logging(debug)
    except ValueError as exc:
        _stderr_fail(str(exc))

    mcp_server.add_middleware(
        ActivityMiddleware(
            "mcp_http" if http else "mcp_stdio", page_run=page_map_subject is not None
        )
    )
    if not http:
        from groundly.mcp.forward import ForwardMiddleware

        # Inside activity, so this process logs a forwarded call and `start` skips it.
        mcp_server.add_middleware(ForwardMiddleware(page_run=page_map_subject is not None))
        atexit.register(activity.record, "mcp_stdio", "session_end")
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
