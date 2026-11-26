"""Tailscale remote session store, single-use QR pairing, and QR helpers."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import segno
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from groundly.core.paths import owner_only, remote_db_path
from groundly.web.tailscale import TailscaleInfo

SESSION_COOKIE = "groundly_session"
SESSION_TTL_S = 30 * 86400
LAST_SEEN_THROTTLE_S = 60
MAX_PAIR_FAILURES = 5
PAIR_WINDOW_S = 60.0

_REMOTE_USER_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash BLOB NOT NULL UNIQUE,
    label TEXT NOT NULL,
    peer_ip TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
"""


def remote_db_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, sqlite3.OperationalError) and ("locked" in str(exc) or "busy" in str(exc)):
        return JSONResponse(
            {"error": "busy", "detail": "remote session store is busy — try again in a moment"},
            status_code=503,
        )
    return JSONResponse(
        {"error": "store_error", "detail": "could not read or update the remote session store"},
        status_code=500,
    )


class PairAuthError(Exception):
    """Error raised when a pairing attempt fails validation or rate limiting."""

    def __init__(self, status_code: int, error: str, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.error = error
        self.detail = detail


def parse_device_label(user_agent: str) -> str:
    """Derive a sanitized 'OS · Browser' label from an untrusted User-Agent string."""
    ua = user_agent or ""

    if "iPad" in ua:
        os_name = "iPad"
    elif "iPhone" in ua:
        os_name = "iPhone"
    elif "Android" in ua:
        os_name = "Android"
    elif "Macintosh" in ua or "Mac OS" in ua:
        os_name = "Mac"
    elif "Windows" in ua:
        os_name = "Windows"
    elif "Linux" in ua:
        os_name = "Linux"
    else:
        os_name = "Device"

    if "Edg/" in ua or "EdgiOS" in ua:
        browser = "Edge"
    elif "FxiOS" in ua or "Firefox" in ua:
        browser = "Firefox"
    elif "CriOS" in ua or "Chrome" in ua:
        browser = "Chrome"
    elif "Safari" in ua:
        browser = "Safari"
    else:
        browser = "Browser"

    return f"{os_name} · {browser}"


def _hash_token(raw_token: str) -> bytes:
    return hashlib.sha256(raw_token.encode("utf-8")).digest()


class RemoteStore:
    """Thread-safe SQLite store for paired Tailscale device sessions."""

    def __init__(
        self,
        db_path: Path | None = None,
        *,
        now: Callable[[], int] | None = None,
    ) -> None:
        self._db_path = db_path if db_path is not None else remote_db_path()
        self._now = now if now is not None else lambda: int(time.time())
        self._lock = threading.Lock()

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._db_path.exists():
            fd = os.open(self._db_path, os.O_RDWR | os.O_CREAT, 0o600)
            os.close(fd)
            os.chmod(self._db_path, 0o600)
        else:
            owner_only(self._db_path)

        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version > _REMOTE_USER_VERSION:
                raise RuntimeError(f"Unsupported remote.db schema version {version}")
            if version == 0:
                conn.executescript(_SCHEMA)
                conn.execute(f"PRAGMA user_version = {_REMOTE_USER_VERSION}")
                conn.commit()
            yield conn
        finally:
            conn.close()

    def create_session(
        self,
        raw_token: str,
        user_agent: str,
        peer_ip: str,
        *,
        replace_raw_token: str | None = None,
    ) -> dict[str, Any]:
        """Store a new sha256-hashed session, optionally superseding an existing token."""
        now = self._now()
        label = parse_device_label(user_agent)
        created_at = datetime.fromtimestamp(now, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        expires_at = now + SESSION_TTL_S
        token_hash = _hash_token(raw_token)

        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            if replace_raw_token:
                conn.execute(
                    "DELETE FROM sessions WHERE token_hash = ?",
                    (_hash_token(replace_raw_token),),
                )
            cursor = conn.execute(
                """
                INSERT INTO sessions (
                    token_hash, label, peer_ip, created_at, last_seen_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (token_hash, label, peer_ip, created_at, now, expires_at),
            )
            device_id = cursor.lastrowid
            conn.commit()

        return {
            "id": device_id,
            "label": label,
            "peer_ip": peer_ip,
            "created_at": created_at,
            "last_seen_at": now,
        }

    def authenticate(self, raw_token: str) -> bool:
        """Validate a raw session cookie token and update throttled last_seen_at."""
        if not raw_token:
            return False
        now = self._now()
        token_hash = _hash_token(raw_token)

        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id, last_seen_at, expires_at FROM sessions WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is None:
                return False
            if int(row["expires_at"]) <= now:
                conn.execute("DELETE FROM sessions WHERE id = ?", (row["id"],))
                conn.commit()
                return False
            if now - int(row["last_seen_at"]) >= LAST_SEEN_THROTTLE_S:
                conn.execute("UPDATE sessions SET last_seen_at = ? WHERE id = ?", (now, row["id"]))
                conn.commit()
            return True

    def list_devices(self) -> list[dict[str, Any]]:
        """Return active paired devices newest-seen first."""
        now = self._now()
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, label, peer_ip, last_seen_at
                FROM sessions
                WHERE expires_at > ?
                ORDER BY last_seen_at DESC, id DESC
                """,
                (now,),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "label": str(row["label"]),
                "peer_ip": str(row["peer_ip"]),
                "last_seen_at": int(row["last_seen_at"]),
            }
            for row in rows
        ]

    def count_devices(self) -> int:
        """Return the number of active paired devices."""
        now = self._now()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE expires_at > ?",
                (now,),
            ).fetchone()
        return int(row[0])

    def revoke_device(self, device_id: int) -> bool:
        """Revoke one device session by id; returns True when a row was deleted."""
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (device_id,))
            conn.commit()
            return cursor.rowcount > 0

    def revoke_all(self) -> int:
        """Revoke all paired device sessions and return the count removed."""
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM sessions")
            conn.commit()
            return cursor.rowcount


def qr_matrix(url: str) -> list[list[bool]]:
    """Encode a pairing URL into a boolean QR module matrix."""
    qr = segno.make(url, error="l")
    return [[bool(cell) for cell in row] for row in qr.matrix]


def render_terminal_qr(url: str) -> str:
    """Render a compact Unicode half-block terminal QR code for a pairing URL."""
    qr = segno.make(url, error="l")
    buf = io.StringIO()
    qr.terminal(out=buf, compact=True, border=2)
    return buf.getvalue()


class RemoteRuntime:
    """Coordinate single-use pairing links, persisted sessions, and active streams."""

    def __init__(
        self,
        ts_info: TailscaleInfo,
        port: int,
        on_paired: Callable[[str, str, str], None] | None = None,
    ) -> None:
        self.ts_info = ts_info
        self.port = port
        self.store = RemoteStore()
        self.on_paired = on_paired
        self._pair_lock = threading.Lock()
        self._active_token = secrets.token_urlsafe(24)
        self._failures: dict[str, deque[float]] = {}
        self._stream_lock = threading.Lock()
        self._stream_events: set[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = set()

    def _pairing_url(self) -> str:
        return f"http://{self.ts_info.preferred_host}:{self.port}/pair#{self._active_token}"

    @property
    def pairing_url(self) -> str:
        with self._pair_lock:
            return self._pairing_url()

    def _check_token(self, candidate: str, peer_ip: str) -> None:
        now = time.monotonic()
        failures = self._failures.get(peer_ip)
        if failures is not None:
            cutoff = now - PAIR_WINDOW_S
            while failures and failures[0] <= cutoff:
                failures.popleft()
            if not failures:
                del self._failures[peer_ip]
                failures = None

        if failures is not None and len(failures) >= MAX_PAIR_FAILURES:
            raise PairAuthError(
                429,
                "rate_limited",
                "too many failed pairing attempts — wait a minute and try again",
            )

        valid = bool(candidate) and hmac.compare_digest(
            candidate.encode("utf-8"),
            self._active_token.encode("utf-8"),
        )
        if not valid:
            self._failures.setdefault(peer_ip, deque()).append(now)
            raise PairAuthError(
                401,
                "invalid_pairing_token",
                "pairing link is invalid or was already used — scan the current QR code",
            )

    def pair_device(
        self, candidate: str, peer_ip: str, user_agent: str, replace_token: str | None
    ) -> str:
        """Publish a replacement QR only after the new session has committed."""
        with self._pair_lock:
            self._check_token(candidate, peer_ip)
            session = secrets.token_urlsafe(32)
            device = self.store.create_session(
                session, user_agent, peer_ip, replace_raw_token=replace_token
            )
            self._active_token = secrets.token_urlsafe(24)
            if replace_token:
                self.notify_revoked()
            next_url = self._pairing_url()
        if self.on_paired is not None:
            # Terminal output is best-effort after the session commits.
            with suppress(OSError, ValueError):
                self.on_paired(device["label"], peer_ip, next_url)
        return session

    def revoke_all(self) -> int:
        with self._pair_lock:
            count = self.store.revoke_all()
            self._active_token = secrets.token_urlsafe(24)
            self.notify_revoked()
            return count

    def register_stream(self, loop: asyncio.AbstractEventLoop, event: asyncio.Event) -> None:
        """Register an active Tailscale SSE stream to wake immediately on device revocation."""
        with self._stream_lock:
            self._stream_events.add((loop, event))

    def unregister_stream(self, loop: asyncio.AbstractEventLoop, event: asyncio.Event) -> None:
        """Remove a closed Tailscale SSE stream from revocation wake-ups."""
        with self._stream_lock:
            self._stream_events.discard((loop, event))

    def notify_revoked(self) -> None:
        """Wake all active Tailscale SSE streams so revoked sessions disconnect immediately."""
        with self._stream_lock:
            targets = list(self._stream_events)
        for loop, event in targets:
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                with self._stream_lock:
                    self._stream_events.discard((loop, event))


_SUBJECT_SHELL_RE = re.compile(r"^/s/[^/]+(?:/test|/attempts/\d+)?$")


def _extract_host(headers: Headers) -> str | None:
    """Extract and lowercase hostname from a single Host header, validating optional port."""
    values = headers.getlist("host")
    if len(values) != 1:
        return None
    raw = values[0].strip()
    if not raw:
        return None
    hostname, sep, port = raw.partition(":")
    if not hostname or (
        sep and (not port.isascii() or not port.isdigit() or not (1 <= int(port) <= 65535))
    ):
        return None
    return hostname.lower()


class _SocketPerimeterMiddleware:
    """Enforce loopback vs Tailscale socket host rules, /mcp isolation, and session cookies."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        loopback_hosts: Sequence[str],
        html_shell: Callable[[], Response],
        remote: RemoteRuntime | None = None,
    ) -> None:
        self._app = app
        self._loopback_hosts = frozenset(host.lower() for host in loopback_hosts)
        self._html_shell = html_shell
        self._remote = remote
        self._tailscale_hosts = (
            frozenset(host.lower() for host in remote.ts_info.allowed_hosts)
            if remote is not None
            else frozenset()
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        server = scope.get("server")
        server_ip = server[0] if server else None
        remote = self._remote
        is_tailscale = remote is not None and server_ip == remote.ts_info.ipv4

        host = _extract_host(Headers(scope=scope))
        allowed_hosts = self._tailscale_hosts if is_tailscale else self._loopback_hosts
        if host is None or host not in allowed_hosts:
            await PlainTextResponse("Invalid host header", status_code=400)(scope, receive, send)
            return

        state = scope.setdefault("state", {})
        if not is_tailscale or remote is None:
            state["access"] = "loopback"
            state["paired"] = True
            await self._app(scope, receive, send)
            return

        state["access"] = "tailscale"
        path = scope.get("path", "")
        if path == "/mcp" or path.startswith("/mcp/"):
            await Response("not found", status_code=404)(scope, receive, send)
            return
        if path == "/api/remote" or path.startswith("/api/remote/"):
            await JSONResponse(
                {"error": "not_found", "detail": "remote management is local only"},
                status_code=404,
            )(scope, receive, send)
            return

        method = scope.get("method", "GET").upper()
        if method in ("GET", "HEAD") and path.startswith("/static/"):
            await self._app(scope, receive, send)
            return
        if method == "POST" and path == "/api/pair":
            await self._app(scope, receive, send)
            return

        shell = method == "GET" and (
            path in {"/", "/pair", "/connections"} or _SUBJECT_SHELL_RE.fullmatch(path) is not None
        )
        session_token = Request(scope).cookies.get(SESSION_COOKIE)
        if session_token:
            try:
                paired = bool(await run_in_threadpool(remote.store.authenticate, session_token))
            except (sqlite3.DatabaseError, RuntimeError) as exc:
                response = self._html_shell() if shell else remote_db_error(exc)
                await response(scope, receive, send)
                return
        else:
            paired = False
        state["paired"] = paired

        if method == "GET" and path == "/api/auth":
            await self._app(scope, receive, send)
            return
        if shell and not paired:
            await self._html_shell()(scope, receive, send)
            return

        if not paired:
            await JSONResponse(
                {
                    "error": "unpaired",
                    "detail": "scan the pairing QR code on your computer",
                },
                status_code=401,
            )(scope, receive, send)
            return

        await self._app(scope, receive, send)
