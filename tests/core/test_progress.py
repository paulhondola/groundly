import json
import sqlite3

import pytest

from groundly.core import progress

# --- traces (progress.db) ---------------------------------------------------------


def test_connect_progress_creates_table_on_fresh_file(tmp_path):
    path = tmp_path / "progress.db"
    conn = progress.connect_progress(path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0] == 0
    finally:
        conn.close()


def test_connect_progress_upgrades_preexisting_empty_progress_db(tmp_path):
    path = tmp_path / "progress.db"
    progress.create_progress(path)  # P1-era empty progress.db, no traces table
    conn = progress.connect_progress(path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0] == 0
    finally:
        conn.close()


def test_record_trace_round_trips(tmp_path):
    path = tmp_path / "progress.db"
    conn = progress.connect_progress(path)
    try:
        progress.record_trace(
            conn,
            kind="ask",
            query="what is a deadlock?",
            router_label="factoid",
            arm="vector",
            path=["dense", "sparse", "bm25", "rrf", "rerank"],
            chunk_ids=[1, 2, 3],
            outcome="answered",
            answer="A deadlock is [chunk 1].",
            citations=[{"chunk_id": 1, "filename": "lec.pdf", "page": 4, "heading_path": None}],
            model="local-model",
            tokens=42,
            cost_usd=0.001,
            latency_ms=250,
        )
        row = conn.execute("SELECT * FROM traces").fetchone()
        assert row["kind"] == "ask"
        assert row["query"] == "what is a deadlock?"
        assert row["router_label"] == "factoid"
        assert row["arm"] == "vector"
        assert json.loads(row["path"]) == ["dense", "sparse", "bm25", "rrf", "rerank"]
        assert json.loads(row["chunk_ids"]) == [1, 2, 3]
        assert row["outcome"] == "answered"
        assert row["answer"] == "A deadlock is [chunk 1]."
        assert json.loads(row["citations"])[0]["filename"] == "lec.pdf"
        assert row["model"] == "local-model"
        assert row["tokens"] == 42
        assert row["cost_usd"] == 0.001
        assert row["latency_ms"] == 250
        assert row["error"] is None
        assert row["ts"] is not None
    finally:
        conn.close()


def test_record_trace_defaults_are_null(tmp_path):
    conn = progress.connect_progress(tmp_path / "progress.db")
    try:
        progress.record_trace(conn, kind="search", query="q", outcome="results")
        row = conn.execute("SELECT * FROM traces").fetchone()
        assert row["router_label"] is None
        assert row["path"] is None
        assert row["chunk_ids"] is None
        assert row["citations"] is None
        assert row["error"] is None
    finally:
        conn.close()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": "bogus", "query": "q", "outcome": "results"},
        {"kind": "ask", "query": "q", "outcome": "bogus"},
    ],
)
def test_record_trace_check_constraints_reject_bad_values(tmp_path, kwargs):
    conn = progress.connect_progress(tmp_path / "progress.db")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            progress.record_trace(conn, **kwargs)
    finally:
        conn.close()
