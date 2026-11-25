"""`groundly serve`: the topic-tree page on loopback; `groundly start`: the page plus the MCP
tools at `/mcp` in one process, so every host session shares one model load. Lazy-import
wrappers — the web app and server load only when a verb runs; the page itself loads no model."""

import errno
import signal
import socket
import threading
import webbrowser
from collections.abc import Awaitable, Callable
from types import FrameType
from typing import TYPE_CHECKING, Annotated

import typer

from groundly.cli.app import _fail, _safe, app, console

if TYPE_CHECKING:
    from groundly.web.remote import RemoteRuntime


def _open_browser(url: str) -> None:
    # A short delay lets uvicorn start listening; daemonized so Ctrl-C exits without waiting.
    timer = threading.Timer(0.5, webbrowser.open, (url,))
    timer.daemon = True
    timer.start()


_PORT = Annotated[int, typer.Option("--port", min=1, max=65535, help="Port to listen on.")]
_TAILSCALE = Annotated[
    bool | None,
    typer.Option(
        "--tailscale/--no-tailscale",
        help="Bind Tailscale interface for remote pairing (default: web.tailscale).",
    ),
]


def _bind_socket(host: str, port: int) -> socket.socket:
    """Bind and listen on `(host, port)` with SO_REUSEADDR, closing on failure."""
    sock = socket.socket()
    try:
        sock.setsockopt(
            socket.SOL_SOCKET, socket.SO_REUSEADDR, 1
        )  # as uvicorn binds: TIME_WAIT is free
        sock.bind((host, port))
        sock.listen(128)
        return sock
    except BaseException:
        sock.close()
        raise


def _on_paired(label: str, peer_ip: str, next_url: str) -> None:
    console.print(
        f"Paired {_safe(label)} ({_safe(peer_ip)}) — next pair link: {next_url}",
        highlight=False,
        soft_wrap=True,
    )


def _resolve_remote(port: int, tailscale: bool | None) -> "RemoteRuntime | None":
    """Resolve Tailscale status from CLI flag/config and build RemoteRuntime when active."""
    from groundly.core.config import load_settings

    want_ts = load_settings().web.tailscale if tailscale is None else tailscale
    if not want_ts:
        return None

    from groundly.web.remote import RemoteRuntime
    from groundly.web.tailscale import discover_tailscale

    ts_info = discover_tailscale()
    if ts_info is None:
        if tailscale is True:
            _fail("Tailscale is unavailable — start Tailscale or pass --no-tailscale")
        console.print(
            "[yellow]Warning:[/yellow] Tailscale is unavailable; serving on 127.0.0.1 only.",
            highlight=False,
            soft_wrap=True,
        )
        return None

    return RemoteRuntime(ts_info, port=port, on_paired=_on_paired)


def _remote_lines(remote: "RemoteRuntime") -> list[str]:
    from groundly.web.remote import render_terminal_qr

    pairing_url = remote.pairing.pairing_url
    return [
        f"Tailscale: http://{remote.ts_info.preferred_host}:{remote.port}/",
        f"Pair URL:  {pairing_url}",
        render_terminal_qr(pairing_url).rstrip("\n"),
    ]


def _run(
    asgi_app: Callable[..., Awaitable[None]],
    port: int,
    lines: list[str],
    remote: "RemoteRuntime | None" = None,
) -> None:
    """Bind sockets, print `lines`, open the page and serve `asgi_app`."""
    import uvicorn

    hosts = ["127.0.0.1"]
    if remote is not None:
        hosts.append(remote.ts_info.ipv4)

    sockets: list[socket.socket] = []
    try:
        bound_host = "127.0.0.1"
        try:
            for host in hosts:
                bound_host = host
                sockets.append(_bind_socket(host, port))
        except OSError as e:
            if e.errno == errno.EADDRINUSE:
                _fail(f"port {port} is already in use — pick another with --port")
            where = f"port {port}" if bound_host == "127.0.0.1" else f"{bound_host}:{port}"
            _fail(f"cannot bind {where}: {e.strerror}")

        for line in lines:
            console.print(line, highlight=False, soft_wrap=True)  # a wrapped command will not paste
        _open_browser(f"http://groundly.localhost:{port}/")
        # uvicorn shuts down gracefully, so the page stops a running `claude`, on SIGINT and
        # SIGTERM only; closing the terminal sends SIGHUP, which takes the SIGTERM path.
        if hasattr(signal, "SIGHUP"):
            signal.signal(signal.SIGHUP, lambda *_: signal.raise_signal(signal.SIGTERM))

        # proxy_headers=False: the write route's Origin check compares against the socket's own
        # scheme and host, which an X-Forwarded-* header must not be able to change.
        # timeout_graceful_shutdown: an open /mcp stream (a host session, or a page run's) would
        # otherwise hold shutdown, and the lifespan that stops the run, until it closes.
        class _PageServer(uvicorn.Server):
            def handle_exit(self, sig: int, frame: FrameType | None) -> None:
                runner = getattr(getattr(self.config.app, "state", None), "runner", None)
                if runner is not None and not self.should_exit:
                    threading.Thread(target=runner.shutdown, daemon=True).start()
                super().handle_exit(sig, frame)

        config = uvicorn.Config(
            asgi_app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            proxy_headers=False,
            timeout_graceful_shutdown=3,
        )
        _PageServer(config).run(sockets=sockets)
    finally:
        for sock in sockets:
            sock.close()


def _page_line(port: int) -> str:
    # The 127.0.0.1 form is for resolvers that do not map *.localhost to loopback.
    return f"Page: http://groundly.localhost:{port}/  (or http://127.0.0.1:{port}/)"


@app.command()
def serve(port: _PORT = 8765, tailscale: _TAILSCALE = None) -> None:
    """Open the topic-tree page for your subjects in the browser."""
    from groundly.web.app import create_app

    remote = _resolve_remote(port, tailscale)
    lines = [_page_line(port)]
    if remote is not None:
        lines.extend(_remote_lines(remote))
    _run(create_app(remote=remote), port, lines, remote=remote)


@app.command()
def start(port: _PORT = 8765, tailscale: _TAILSCALE = None) -> None:
    """Serve the page and the MCP tools (at /mcp) from one process, for every host session."""
    from groundly.mcp.server import mcp
    from groundly.web.app import HOSTS, create_app

    remote = _resolve_remote(port, tailscale)
    # host_origin_protection stays on: FastMCP's own DNS-rebinding guard, scoped to HOSTS.
    mcp_app = mcp.http_app(path="/mcp", host_origin_protection="auto", allowed_hosts=HOSTS)
    # The IP, not groundly.localhost: an MCP client resolves through the OS, which on some
    # Linux setups forwards *.localhost to upstream DNS that could point it elsewhere.
    mcp_url = f"http://127.0.0.1:{port}/mcp"
    lines = [
        _page_line(port),
        f"MCP:  {mcp_url}",
        f"Connect Claude Code: claude mcp add --scope user --transport http groundly {mcp_url}",
    ]
    if remote is not None:
        lines.extend(_remote_lines(remote))
    _run(create_app(mcp_app, mcp_url=mcp_url, remote=remote), port, lines, remote=remote)
