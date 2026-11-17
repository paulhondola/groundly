"""`groundly serve`: the topic-tree page on loopback; `groundly start`: the page plus the MCP
tools at `/mcp` in one process, so every host session shares one model load. Lazy-import
wrappers — the web app and server load only when a verb runs; the page itself loads no model."""

import errno
import socket
import threading
import webbrowser
from typing import Annotated

import typer

from groundly.cli.app import _fail, app, console


def _open_browser(url: str) -> None:
    # A short delay lets uvicorn start listening; daemonized so Ctrl-C exits without waiting.
    timer = threading.Timer(0.5, webbrowser.open, (url,))
    timer.daemon = True
    timer.start()


_PORT = Annotated[
    int, typer.Option("--port", min=1, max=65535, help="Port to listen on (127.0.0.1 only).")
]


def _run(asgi_app: object, port: int, lines: list[str]) -> None:
    """Probe the port, print `lines`, open the page and serve `asgi_app` on 127.0.0.1."""
    import uvicorn

    # Probe first so a busy port gets a plain message instead of uvicorn's traceback.
    with socket.socket() as probe:
        probe.setsockopt(
            socket.SOL_SOCKET, socket.SO_REUSEADDR, 1
        )  # as uvicorn binds: TIME_WAIT is free
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as e:
            if e.errno == errno.EADDRINUSE:
                _fail(f"port {port} is already in use — pick another with --port")
            _fail(f"cannot bind port {port}: {e.strerror}")
    for line in lines:
        console.print(line, highlight=False, soft_wrap=True)  # a wrapped command will not paste
    _open_browser(f"http://groundly.localhost:{port}/")
    # proxy_headers=False: the write route's Origin check compares against the socket's own
    # scheme and host, which an X-Forwarded-* header must not be able to change.
    uvicorn.run(asgi_app, host="127.0.0.1", port=port, log_level="warning", proxy_headers=False)


def _page_line(port: int) -> str:
    # The 127.0.0.1 form is for resolvers that do not map *.localhost to loopback.
    return f"Page: http://groundly.localhost:{port}/  (or http://127.0.0.1:{port}/)"


@app.command()
def serve(port: _PORT = 8765) -> None:
    """Open the topic-tree page for your subjects in the browser."""
    from groundly.web.app import create_app

    _run(create_app(), port, [_page_line(port)])


@app.command()
def start(port: _PORT = 8765) -> None:
    """Serve the page and the MCP tools (at /mcp) from one process, for every host session."""
    from groundly.mcp.server import mcp
    from groundly.web.app import HOSTS, create_app

    # host_origin_protection stays on: FastMCP's own DNS-rebinding guard, scoped to HOSTS.
    mcp_app = mcp.http_app(path="/mcp", host_origin_protection="auto", allowed_hosts=HOSTS)
    mcp_url = f"http://groundly.localhost:{port}/mcp"
    _run(
        create_app(mcp_app),
        port,
        [
            _page_line(port),
            f"MCP:  {mcp_url}",
            f"Connect Claude Code: claude mcp add --transport http groundly {mcp_url}",
        ],
    )
