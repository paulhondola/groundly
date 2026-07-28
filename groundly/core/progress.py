"""progress.db access — traces, verification outcomes, and the study history built on
top of them.

**Never exported.** The privacy boundary is a file
(.claude/rules/grounding-and-privacy.md): progress.db never travels in a bundle and
export code never reads it. core/bundle.py imports nothing from this module, by
design — keep it that way.

No PRAGMA user_version gate here, unlike store.db: progress.db never travels, so its
schema grows locally through CREATE TABLE IF NOT EXISTS with no interchange impact.
Every connection still gets WAL + busy_timeout — one-shot CLI runs and host-spawned
MCP processes share the file (.claude/rules/architecture.md).
"""

import json
import sqlite3
from pathlib import Path

_TRACES_SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('ask', 'search', 'index')),
    query TEXT NOT NULL,
    router_label TEXT,
    arm TEXT,
    path TEXT,       -- JSON array, e.g. ["dense","sparse","bm25","rrf","rerank"]
    chunk_ids TEXT,  -- JSON array of retrieved chunk ids
    outcome TEXT NOT NULL CHECK (outcome IN ('answered', 'refused', 'error', 'results', 'built')),
    answer TEXT,
    citations TEXT,  -- JSON array of {chunk_id, filename, page, heading_path}
    model TEXT,
    tokens INTEGER,
    cost_usd REAL,
    latency_ms INTEGER,
    error TEXT,
    ts TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def create_progress(path: Path) -> None:
    # Tables arrive in P3 (traces) / P6 (quiz_events, notes); progress.db never
    # travels, so its schema can grow locally without interchange impact.
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    finally:
        conn.close()


_VERIFICATIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS verifications (
    id INTEGER PRIMARY KEY,
    generation_source TEXT NOT NULL CHECK (generation_source IN ('server', 'host')),
    reason TEXT,  -- a REJECTION_REASONS value; NULL = accepted
    ts TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def connect_progress(path: Path) -> sqlite3.Connection:
    """Open progress.db, creating it (and the traces/verifications tables) if missing.
    `CREATE TABLE IF NOT EXISTS` idempotently upgrades a pre-existing progress.db
    (P1/P2 era) with no migration framework — progress.db never travels, so this is
    safe."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.executescript(_TRACES_SCHEMA)
    conn.executescript(_VERIFICATIONS_SCHEMA)
    conn.commit()
    return conn


def record_verification(
    conn: sqlite3.Connection, *, generation_source: str, reason: str | None
) -> None:
    """One row per verifier verdict, from either door — the rejection-rate-by-source
    measurement (docs/architecture/data-model.md). reason=None records an accept."""
    with conn:
        conn.execute(
            "INSERT INTO verifications (generation_source, reason) VALUES (?, ?)",
            (generation_source, reason),
        )


def record_trace(
    conn: sqlite3.Connection,
    *,
    kind: str,
    query: str,
    outcome: str,
    router_label: str | None = None,
    arm: str | None = None,
    path: list[str] | None = None,
    chunk_ids: list[int] | None = None,
    answer: str | None = None,
    citations: list[dict] | None = None,
    model: str | None = None,
    tokens: int | None = None,
    cost_usd: float | None = None,
    latency_ms: int | None = None,
    error: str | None = None,
) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO traces (
                kind, query, router_label, arm, path, chunk_ids, outcome,
                answer, citations, model, tokens, cost_usd, latency_ms, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                kind,
                query,
                router_label,
                arm,
                json.dumps(path) if path is not None else None,
                json.dumps(chunk_ids) if chunk_ids is not None else None,
                outcome,
                answer,
                json.dumps(citations) if citations is not None else None,
                model,
                tokens,
                cost_usd,
                latency_ms,
                error,
            ),
        )
