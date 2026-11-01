"""`groundly serve`: the read-only topic-tree page on loopback. A lazy-import wrapper — the web
app loads only when the verb runs; serving never loads a model."""

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


@app.command()
def serve(
    port: Annotated[
        int, typer.Option("--port", min=1, max=65535, help="Port to listen on (127.0.0.1 only).")
    ] = 8765,
) -> None:
    """Open the topic-tree page for your subjects in the browser."""
    import uvicorn

    from groundly.web.app import create_app

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
    url = f"http://127.0.0.1:{port}/"
    console.print(f"Serving the topic tree at {url}")
    _open_browser(url)
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")
