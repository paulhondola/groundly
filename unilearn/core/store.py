"""store.db / progress.db access. Schema versioned via PRAGMA user_version — no
migration framework; refuse to open a newer schema than this tool understands.

Every connection gets WAL + busy_timeout: one-shot CLI runs and host-spawned MCP
processes share the same files (.claude/rules/architecture.md).
"""

import sqlite3
from pathlib import Path

import sqlite_vec

from unilearn.core.manifest import EMBEDDING_DIM

STORE_USER_VERSION = 1

_SCHEMA = f"""
CREATE TABLE materials (
    id INTEGER PRIMARY KEY,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('indexed', 'extraction_failed')),
    pages INTEGER,
    error TEXT,
    indexed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE chunks (
    id INTEGER PRIMARY KEY,
    material_id INTEGER NOT NULL REFERENCES materials(id) ON DELETE CASCADE,
    page INTEGER,
    heading_path TEXT,
    text TEXT NOT NULL,
    token_count INTEGER
);
CREATE INDEX idx_chunks_material ON chunks(material_id);

-- rowid = chunks.id; vec0 has no FK support, deletion is explicit in remove_material
CREATE VIRTUAL TABLE vectors USING vec0(embedding float[{EMBEDDING_DIM}]);

CREATE TABLE sparse_terms (
    token_id INTEGER NOT NULL,
    chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    weight REAL NOT NULL
);
CREATE INDEX idx_sparse_token ON sparse_terms(token_id);
CREATE INDEX idx_sparse_chunk ON sparse_terms(chunk_id);

CREATE VIRTUAL TABLE chunks_fts USING fts5(text, content='chunks', content_rowid='id');
CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
"""


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > STORE_USER_VERSION:
        conn.close()
        raise RuntimeError(
            f"{path.name} has schema version {version}, newer than this unilearn "
            f"understands (max {STORE_USER_VERSION}) — upgrade unilearn"
        )
    return conn


def create_store(path: Path) -> None:
    conn = connect(path)
    try:
        conn.executescript(_SCHEMA)
        conn.execute(f"PRAGMA user_version = {STORE_USER_VERSION}")
        conn.commit()
    finally:
        conn.close()


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


def list_materials(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT m.id, m.filename, m.sha256, m.status, m.pages, m.error,
               COUNT(c.id) AS chunk_count
        FROM materials m LEFT JOIN chunks c ON c.material_id = m.id
        GROUP BY m.id ORDER BY m.filename
        """
    ).fetchall()


def hash_status(conn: sqlite3.Connection) -> dict[str, str]:
    """sha256 -> status, for hash-skip (indexed) and retry (extraction_failed)."""
    return {r["sha256"]: r["status"] for r in conn.execute("SELECT sha256, status FROM materials")}


def find_materials(conn: sqlite3.Connection, ident: str) -> list[sqlite3.Row]:
    """Match by exact filename or sha256 prefix (the disambiguator)."""
    escaped = ident.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return conn.execute(
        "SELECT * FROM materials WHERE filename = ? OR sha256 LIKE ? ESCAPE '\\' ORDER BY filename",
        (ident, escaped + "%"),
    ).fetchall()


def remove_material(conn: sqlite3.Connection, material_id: int) -> None:
    """One transaction. FTS syncs via the chunks_ad trigger; sparse_terms via FK
    cascade; vectors (vec0, no FK) deleted explicitly by chunk rowid."""
    with conn:
        chunk_ids = [
            r["id"]
            for r in conn.execute("SELECT id FROM chunks WHERE material_id = ?", (material_id,))
        ]
        conn.executemany("DELETE FROM vectors WHERE rowid = ?", [(cid,) for cid in chunk_ids])
        conn.execute("DELETE FROM chunks WHERE material_id = ?", (material_id,))
        conn.execute("DELETE FROM materials WHERE id = ?", (material_id,))
