"""activity.db is best-effort shared state between Groundly processes: these tests pin what
it records, how the page reads it, and that it never fails the work it describes."""

import os
import sqlite3
import stat
from concurrent.futures import ProcessPoolExecutor

import pytest

from groundly.core import activity

T0 = 1_800_000_000.0


@pytest.fixture
def db(tmp_path):
    return tmp_path / "activity.db"


def rec(db, source, event, at, *, pid=111, **kw):
    activity.record(source, event, path=db, now=at, pid=pid, **kw)


def snap(db, at, alive=(111,), **kw):
    return activity.snapshot(path=db, now=at, pid_alive=lambda p: p in alive, **kw)


def test_record_creates_a_private_wal_file_on_first_write(db):
    rec(db, "mcp_stdio", "session_start", T0, client="claude-code")
    assert stat.S_IMODE(db.stat().st_mode) == 0o600
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == activity.ACTIVITY_USER_VERSION
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()


def test_snapshot_of_a_missing_file_is_empty_and_creates_nothing(db):
    assert snap(db, T0) == {"hosts": [], "indexing": [], "recent": []}
    assert not db.exists()


def test_record_never_raises_and_a_broken_file_reads_as_empty(db):
    db.mkdir()
    rec(db, "mcp_stdio", "session_start", T0)  # a directory where the file should be
    db.rmdir()
    db.write_bytes(b"not a database")
    rec(db, "mcp_stdio", "session_start", T0)
    assert snap(db, T0) == {"hosts": [], "indexing": [], "recent": []}


def test_stdio_host_is_live_while_its_pid_is(db):
    rec(db, "mcp_stdio", "session_start", T0, client="claude-code")
    (host,) = snap(db, T0 + 1)["hosts"]
    assert host["client"] == "claude-code" and host["transport"] == "stdio"
    assert host["orb"] == "listening" and host["last_call_at"] is None
    assert snap(db, T0 + 1, alive=())["hosts"] == []


def test_http_host_expires_after_ten_minutes(db):
    rec(db, "mcp_http", "session_start", T0, session="s1", client="codex")
    rec(
        db,
        "mcp_http",
        "tool_call",
        T0 + 10,
        session="s2",
        client="codex",
        detail={"tool": "search"},
    )
    rec(
        db, "mcp_http", "tool_end", T0 + 11, session="s3", client="codex", detail={"tool": "search"}
    )
    assert snap(db, T0 + 11 + activity.HTTP_LIVE_SECONDS)["hosts"]
    assert snap(db, T0 + 12 + activity.HTTP_LIVE_SECONDS)["hosts"] == []


def test_http_hosts_are_one_per_client_whatever_the_session_ids(db):
    # 2026-07-28 requests each get a new session id; only the client name is stable.
    for i, client in enumerate(["claude-code", "codex", "claude-code"]):
        rec(db, "mcp_http", "tool_call", T0 + i, session=f"s{i}", client=client,
            detail={"tool": "search"})  # fmt: skip
    assert sorted(h["client"] for h in snap(db, T0 + 3)["hosts"]) == ["claude-code", "codex"]


def test_session_end_removes_the_host(db):
    rec(db, "mcp_stdio", "session_start", T0)
    rec(db, "mcp_stdio", "session_end", T0 + 5)
    assert snap(db, T0 + 6)["hosts"] == []


def test_orb_holds_an_unfinished_tool_then_lingers_then_listens(db):
    rec(db, "mcp_stdio", "session_start", T0)
    rec(db, "mcp_stdio", "tool_call", T0 + 1, subject="apd", detail={"tool": "submit_tree"})
    (host,) = snap(db, T0 + 60)["hosts"]
    assert host["orb"] == "mapping" and host["subject"] == "apd"
    rec(db, "mcp_stdio", "tool_end", T0 + 60, subject="apd", detail={"tool": "submit_tree"})
    assert snap(db, T0 + 60 + activity.LINGER_SECONDS)["hosts"][0]["orb"] == "mapping"
    assert snap(db, T0 + 61 + activity.LINGER_SECONDS)["hosts"][0]["orb"] == "listening"


@pytest.mark.parametrize(
    ("tool", "orb"),
    [("search", "reading"), ("get_page", "reading"), ("submit_questions", "writing")],
)
def test_each_tool_maps_to_its_orb(db, tool, orb):
    rec(db, "mcp_stdio", "tool_call", T0, detail={"tool": tool})
    assert snap(db, T0 + 1)["hosts"][0]["orb"] == orb


def test_last_done_at_is_the_newest_accepted_event(db):
    rec(db, "mcp_stdio", "session_start", T0)
    assert snap(db, T0)["hosts"][0]["last_done_at"] is None
    rec(db, "mcp_stdio", "tree_accepted", T0 + 3, subject="apd")
    rec(db, "mcp_stdio", "answers_reviewed", T0 + 9, subject="apd", detail={"count": 2})
    assert snap(db, T0 + 10)["hosts"][0]["last_done_at"] == activity.iso(T0 + 9)


def test_indexing_lists_live_unfinished_runs_with_progress(db):
    rec(db, "index", "index_start", T0, subject="bia", detail={"files": 3})
    rec(db, "index", "index_file", T0 + 2, subject="bia",
        detail={"n": 2, "of": 3, "file": "b.pdf", "status": "indexed"})  # fmt: skip
    (run,) = snap(db, T0 + 3)["indexing"]
    assert run == {
        "subject": "bia",
        "done": 2,
        "total": 3,
        "current_file": "b.pdf",
        "started_at": activity.iso(T0),
    }
    assert snap(db, T0 + 3, alive=())["indexing"] == []
    rec(db, "index", "index_done", T0 + 4, subject="bia", detail={"indexed": 3, "failed": 0})
    assert snap(db, T0 + 5)["indexing"] == []


def test_recent_texts_are_newest_first_and_capped(db):
    rec(db, "mcp_stdio", "tree_accepted", T0, subject="passc", detail={"topics": 3})
    rec(db, "mcp_stdio", "questions_accepted", T0 + 1, subject="apd",
        detail={"topic_id": 7, "count": 10})  # fmt: skip
    rec(db, "mcp_stdio", "answers_reviewed", T0 + 2, subject="apd", detail={"count": 1})
    rec(db, "index", "index_done", T0 + 3, subject="apd", detail={"indexed": 4, "failed": 0})
    recent = snap(db, T0 + 4, topic_title=lambda s, t: "OpenMP" if (s, t) == ("apd", 7) else None)
    assert [(r["subject"], r["text"]) for r in recent["recent"]] == [
        ("apd", "Indexed 4 files"),
        ("apd", "Graded 1 answer"),
        ("apd", "Wrote 10 questions · OpenMP"),
        ("passc", "Built topic map"),
    ]
    for i in range(25):
        rec(db, "mcp_stdio", "tree_accepted", T0 + 10 + i, subject="apd")
    assert len(snap(db, T0 + 40)["recent"]) == activity.RECENT_LIMIT


def test_old_rows_are_pruned_on_session_start(db):
    rec(db, "mcp_stdio", "tree_accepted", T0, subject="apd")
    rec(db, "mcp_stdio", "session_start", T0 + activity.RETENTION_SECONDS + 1, pid=222)
    conn = sqlite3.connect(db)
    try:
        assert [r[0] for r in conn.execute("SELECT event FROM events")] == ["session_start"]
    finally:
        conn.close()


def test_windows_liveness_falls_back_to_recency(db, monkeypatch):
    monkeypatch.setattr(activity, "_WINDOWS", True)
    rec(db, "mcp_stdio", "session_start", T0, pid=os.getpid())
    assert activity.snapshot(path=db, now=T0 + 1)["hosts"]
    assert activity.snapshot(path=db, now=T0 + 1 + activity.HTTP_LIVE_SECONDS)["hosts"] == []


def _write_many(args):
    path, pid = args
    for i in range(200):
        activity.record("mcp_stdio", "tool_call", path=path, pid=pid, detail={"tool": "search"})
    return pid


def test_two_processes_write_at_once(db):
    activity.record("mcp_stdio", "session_start", path=db)  # create the file first
    with ProcessPoolExecutor(max_workers=2) as pool:
        list(pool.map(_write_many, [(db, 1), (db, 2)]))
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM events WHERE event='tool_call'").fetchone() == (
            400,
        )
    finally:
        conn.close()


def _create_and_write(args):
    path, pid = args
    activity.record("index", "index_start", path=path, pid=pid, detail={"files": 1})
    return pid


def test_processes_creating_the_file_at_once_lose_no_rows(tmp_path):
    # Switching a fresh file to WAL takes an exclusive lock that busy_timeout does not wait on.
    paths = [tmp_path / f"a{i}.db" for i in range(30)]
    with ProcessPoolExecutor(max_workers=6) as pool:
        for path in paths:
            list(pool.map(_create_and_write, [(path, pid) for pid in range(1, 7)]))
    for path in paths:
        conn = sqlite3.connect(path)
        try:
            assert conn.execute("SELECT COUNT(*) FROM events").fetchone() == (6,), path.name
        finally:
            conn.close()


def test_http_host_goes_with_its_server_process(db):
    rec(db, "mcp_http", "tool_call", T0, client="codex", detail={"tool": "search"})
    assert snap(db, T0 + 1)["hosts"]
    assert snap(db, T0 + 1, alive=())["hosts"] == []


def test_an_index_run_that_indexed_nothing_is_not_recent(db):
    rec(db, "index", "index_done", T0, subject="apd", detail={"indexed": 0, "failed": 0})
    rec(db, "index", "index_done", T0 + 1, subject="apd", detail={"indexed": 0, "failed": 2})
    assert snap(db, T0 + 2)["recent"] == []


def test_record_tightens_an_existing_file_to_owner_only(db):
    rec(db, "mcp_stdio", "session_start", T0)
    db.chmod(0o644)
    rec(db, "mcp_stdio", "tool_call", T0 + 1, detail={"tool": "search"})
    assert stat.S_IMODE(db.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("at", "detail"), [(T0, "not json"), (T0, "[1]"), (T0, "null"), ("soon", "{}")]
)
def test_a_malformed_row_is_skipped_not_fatal(db, at, detail):
    rec(db, "mcp_stdio", "session_start", T0, client="claude-code")
    conn = sqlite3.connect(db)
    try:
        with conn:
            conn.execute(
                "INSERT INTO events (at, pid, source, event, detail)"
                " VALUES (?, 111, 'mcp_stdio', 'tool_call', ?)",
                (at, detail),
            )
    finally:
        conn.close()
    rec(db, "index", "index_done", T0 + 2, subject="apd", detail={"indexed": 1})
    out = snap(db, T0 + 3)
    assert [h["orb"] for h in out["hosts"]] == ["listening"]
    assert [r["text"] for r in out["recent"]] == ["Indexed 1 file"]
