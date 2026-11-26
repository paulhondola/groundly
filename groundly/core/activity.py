"""activity.db: what Groundly's processes are doing, for the page's host card, orb and indexing strip.

MCP servers (stdio and `/mcp`) and `groundly index` append events; the page reads a snapshot.
Every write is best-effort and never fails the work it describes. The file is private local
state: never exported, never read by export or import, and it holds no course text (valid
subject names, Groundly's tool names, counts, file names, an index run's failure message and the
host's capped self-reported name only; callers check what they pass).
"""

import functools
import json
import logging
import os
import sqlite3
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from groundly.core import paths

SOURCES = ("mcp_stdio", "mcp_http", "index")
ACTIVITY_USER_VERSION = 1
RETENTION_SECONDS = 7 * 86400
HTTP_LIVE_SECONDS = 600
LINGER_SECONDS = 5
RECENT_LIMIT = 20

TOOL_ORB = {
    "search": "reading",
    "get_page": "reading",
    "outline": "reading",
    "topic": "reading",
    "progress": "reading",
    "list_subjects": "reading",
    "submit_tree": "mapping",
    "submit_questions": "writing",
    "remove_questions": "writing",
    "review_answers": "writing",
}

_ACCEPTED = ("tree_accepted", "questions_accepted", "answers_reviewed")
_PRUNE_ON = ("session_start", "index_start")
# os.kill(pid, 0) terminates the process on Windows, so liveness there is recency only.
_WINDOWS = os.name == "nt"

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS events (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  at      REAL    NOT NULL,
  pid     INTEGER NOT NULL,
  source  TEXT    NOT NULL CHECK (source IN ('mcp_stdio','mcp_http','index')),
  session TEXT,
  client  TEXT,
  subject TEXT,
  event   TEXT    NOT NULL,
  detail  TEXT    NOT NULL DEFAULT '{{}}'
);
CREATE INDEX IF NOT EXISTS idx_events_at ON events(at);
PRAGMA user_version = {ACTIVITY_USER_VERSION};
"""

_log = logging.getLogger(__name__)
_EMPTY = {"hosts": [], "indexing": [], "recent": []}


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="milliseconds")


def _create(path: Path) -> None:
    """Make the file whole, in WAL mode, before anyone can open it: switching a shared fresh file
    to WAL takes an exclusive lock that busy_timeout does not wait on, so two processes creating
    it at once would drop a row. The loser of the link race uses the winner's file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".activity-")  # mode 0600
    os.close(fd)
    try:
        conn = sqlite3.connect(tmp)
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(_SCHEMA)
        finally:
            conn.close()
        try:
            os.link(tmp, path)
        except FileExistsError:
            pass
    finally:
        os.unlink(tmp)


def _connect(path: Path, *, create: bool) -> sqlite3.Connection | None:
    """Open the log, or None when it holds no schema."""
    if create and not path.exists():
        _create(path)
    conn = sqlite3.connect(path, timeout=5)
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > ACTIVITY_USER_VERSION:
            raise RuntimeError(f"activity.db is version {version}, newer than this Groundly")
    except BaseException:
        conn.close()
        raise
    if version == 0:
        conn.close()
        return None
    return conn


def record(
    source: str,
    event: str,
    *,
    subject: str | None = None,
    client: str | None = None,
    session: str | None = None,
    detail: dict | None = None,
    path: Path | None = None,
    now: float | None = None,
    pid: int | None = None,
) -> None:
    """Append one event. Never raises: a broken log must not fail a tool call or an index run."""
    try:
        conn = _connect(path or paths.activity_db_path(), create=True)
        if conn is None:
            raise RuntimeError("activity.db holds no schema")
        at = time.time() if now is None else now
        try:
            with conn:
                if event in _PRUNE_ON:
                    conn.execute("DELETE FROM events WHERE at < ?", (at - RETENTION_SECONDS,))
                conn.execute(
                    "INSERT INTO events (at, pid, source, session, client, subject, event, detail)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        at,
                        os.getpid() if pid is None else pid,
                        source,
                        session,
                        client,
                        subject,
                        event,
                        json.dumps(detail or {}),
                    ),
                )
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - the log must never fail the work it describes
        _log.warning("activity log not written (%s): %s", event, exc)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _recent_text(event: str, subject: str | None, detail: dict, title) -> str:
    if event == "tree_accepted":
        return "Built topic map"
    if event == "answers_reviewed":
        return f"Graded {_plural(detail.get('count', 0), 'answer')}"
    if event == "index_done":
        return f"Indexed {_plural(detail.get('indexed', 0), 'file')}"
    text = f"Wrote {_plural(detail.get('count', 0), 'question')}"
    topic_id = detail.get("topic_id")
    name = title(subject, topic_id) if subject and isinstance(topic_id, int) else None
    return f"{text} · {name}" if name else text


def snapshot(
    *,
    path: Path | None = None,
    now: float | None = None,
    pid_alive: Callable[[int], bool] | None = None,
    topic_title: Callable[[str, int], str | None] | None = None,
) -> dict:
    """The `GET /api/activity` body: live hosts with their orb, live index runs, recent results.

    A missing or unreadable log reads as empty and is never created here.
    """
    path = path or paths.activity_db_path()
    if not path.exists():
        return {k: [] for k in _EMPTY}
    now = time.time() if now is None else now
    if pid_alive is None and not _WINDOWS:
        pid_alive = _pid_alive
    title = functools.cache(topic_title) if topic_title else (lambda s, t: None)
    try:
        conn = _connect(path, create=False)
        if conn is None:
            return {k: [] for k in _EMPTY}
        try:
            # ponytail: reads the week's rows on every poll; add a per-host summary table if
            # this shows up in a profile.
            rows = conn.execute(
                "SELECT at, pid, source, session, client, subject, event, detail"
                " FROM events ORDER BY id"
            ).fetchall()
        finally:
            conn.close()
    except (sqlite3.Error, OSError, RuntimeError) as exc:
        _log.warning("activity log not read: %s", exc)
        return {k: [] for k in _EMPTY}

    # ponytail: pid reuse can make a dead stdio host look alive until the 7-day prune; add a
    # start-time check if it shows up.
    def live(source: str, pid: int, last_at: float) -> bool:
        alive = pid_alive is None or pid_alive(pid)
        if source == "mcp_http" or pid_alive is None:
            # An HTTP server outlives its hosts: recency says the host, the pid the server.
            return alive and now - last_at <= HTTP_LIVE_SECONDS
        return alive

    hosts: dict[tuple, dict] = {}
    runs: dict[int, dict] = {}
    recent: list[tuple[float, str, str | None, dict]] = []
    for at, pid, source, session, client, subject, event, raw in rows:
        detail = json.loads(raw)
        if event in _ACCEPTED or (event == "index_done" and detail.get("indexed")):
            recent.append((at, event, subject, detail))
        if source == "index":
            if event == "index_start":
                runs[pid] = {
                    "subject": subject,
                    "done": 0,
                    "total": detail.get("files", 0),
                    "current_file": None,
                    "started_at": iso(at),
                    "last": at,
                }
            elif event == "index_file" and pid in runs:
                runs[pid].update(
                    done=detail.get("n", 0),
                    total=detail.get("of", 0),
                    current_file=detail.get("file"),
                    last=at,
                )
            elif event in ("index_done", "index_failed"):
                runs.pop(pid, None)
            continue
        # stdio: one host per process. http: per client name, since 2026-07-28 requests each
        # carry a fresh session id.
        key = (source, pid, client if source == "mcp_http" else None)
        if event == "session_end":
            hosts.pop(key, None)
            continue
        # Any event opens a host, so one whose session_start was pruned stays visible.
        h = hosts.setdefault(
            key,
            {"source": source, "pid": pid, "started_at": iso(at), "open": [], "end": None,
             "client": None, "subject": None, "last": at, "last_call": None, "done": None},
        )  # fmt: skip
        h["last"] = at
        h["client"] = client or h["client"]
        h["subject"] = subject or h["subject"]
        tool = detail.get("tool")
        if event == "tool_call":
            h["open"].append(tool)
            h["last_call"] = at
        elif event == "tool_end":
            if tool in h["open"]:
                h["open"].remove(tool)
            h["end"] = (tool, at)
        elif event in _ACCEPTED:
            h["done"] = at

    out_hosts = []
    for h in hosts.values():
        if not live(h["source"], h["pid"], h["last"]):
            continue
        if h["open"]:
            orb = TOOL_ORB.get(h["open"][-1], "reading")
        elif h["end"] and now - h["end"][1] <= LINGER_SECONDS:
            orb = TOOL_ORB.get(h["end"][0], "reading")
        else:
            orb = "listening"
        out_hosts.append(
            {
                "client": h["client"],
                "transport": "stdio" if h["source"] == "mcp_stdio" else "http",
                "started_at": h["started_at"],
                "last_call_at": iso(h["last_call"]) if h["last_call"] else None,
                "subject": h["subject"],
                "orb": orb,
                "last_done_at": iso(h["done"]) if h["done"] else None,
            }
        )
    indexing = [
        {k: r[k] for k in ("subject", "done", "total", "current_file", "started_at")}
        for pid, r in runs.items()
        if live("index", pid, r["last"])
    ]
    newest = [
        {"at": iso(at), "subject": subject, "text": _recent_text(event, subject, detail, title)}
        for at, event, subject, detail in recent[::-1][:RECENT_LIMIT]
    ]
    return {"hosts": out_hosts, "indexing": indexing, "recent": newest}
