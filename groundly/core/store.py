"""store.db / progress.db access. Schema versioned via PRAGMA user_version — no
migration framework; refuse to open a newer schema than this tool understands.

Every connection gets WAL + busy_timeout: one-shot CLI runs and host-spawned MCP
processes share the same files (.claude/rules/architecture.md).
"""

import json
import sqlite3
from pathlib import Path

import sqlite_vec

from groundly.core.manifest import EMBEDDING_DIM

STORE_USER_VERSION = 1

_TRACES_SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('ask', 'search')),
    query TEXT NOT NULL,
    router_label TEXT,
    arm TEXT,
    path TEXT,       -- JSON array, e.g. ["dense","sparse","bm25","rrf","rerank"]
    chunk_ids TEXT,  -- JSON array of retrieved chunk ids
    outcome TEXT NOT NULL CHECK (outcome IN ('answered', 'refused', 'error', 'results')),
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


def connect(path: Path, create: bool = False) -> sqlite3.Connection:
    if not create and not path.exists():
        # sqlite3.connect would silently create an empty db — surface the real cause
        raise RuntimeError(f"{path.name} is missing from {path.parent} — the subject is damaged")
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
            f"{path.name} has schema version {version}, newer than this groundly "
            f"understands (max {STORE_USER_VERSION}) — upgrade groundly"
        )
    return conn


def create_store(path: Path) -> None:
    conn = connect(path, create=True)
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


def connect_progress(path: Path) -> sqlite3.Connection:
    """Open progress.db, creating it (and the traces table) if missing. `CREATE TABLE
    IF NOT EXISTS` idempotently upgrades a pre-existing empty progress.db (P1/P2 era)
    with no migration framework — progress.db never travels, so this is safe."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.executescript(_TRACES_SCHEMA)
    conn.commit()
    return conn


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


class SQLiteSubjectStore:
    """A subject's store.db: connection lifecycle + all reads/writes for materials,
    chunks, vectors and sparse terms."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        return connect(self.db_path)

    def list_materials(self) -> list[sqlite3.Row]:
        conn = self.connect()
        try:
            return conn.execute(
                """
                SELECT m.id, m.filename, m.sha256, m.status, m.pages, m.error,
                       COUNT(c.id) AS chunk_count
                FROM materials m LEFT JOIN chunks c ON c.material_id = m.id
                GROUP BY m.id ORDER BY m.filename
                """
            ).fetchall()
        finally:
            conn.close()

    def hash_status(self) -> dict[str, str]:
        """sha256 -> status, for hash-skip (indexed) and retry (extraction_failed)."""
        conn = self.connect()
        try:
            return {
                r["sha256"]: r["status"]
                for r in conn.execute("SELECT sha256, status FROM materials")
            }
        finally:
            conn.close()

    def find_materials(self, ident: str) -> list[sqlite3.Row]:
        """Match by exact filename or sha256 prefix (the disambiguator)."""
        conn = self.connect()
        try:
            escaped = ident.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            return conn.execute(
                "SELECT * FROM materials WHERE filename = ? OR sha256 LIKE ? ESCAPE '\\' "
                "ORDER BY filename",
                (ident, escaped + "%"),
            ).fetchall()
        finally:
            conn.close()

    def remove_material(self, material_id: int) -> None:
        """One transaction. FTS syncs via the chunks_ad trigger; sparse_terms via FK
        cascade; vectors (vec0, no FK) deleted explicitly by chunk rowid."""
        conn = self.connect()
        try:
            with conn:
                chunk_ids = [
                    r["id"]
                    for r in conn.execute(
                        "SELECT id FROM chunks WHERE material_id = ?", (material_id,)
                    )
                ]
                conn.executemany(
                    "DELETE FROM vectors WHERE rowid = ?", [(cid,) for cid in chunk_ids]
                )
                conn.execute("DELETE FROM chunks WHERE material_id = ?", (material_id,))
                conn.execute("DELETE FROM materials WHERE id = ?", (material_id,))
        finally:
            conn.close()

    def add_extraction_failed(self, filename: str, sha256: str, error: str) -> None:
        conn = self.connect()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO materials (filename, sha256, status, error) "
                    "VALUES (?, ?, 'extraction_failed', ?)",
                    (filename, sha256, error),
                )
        finally:
            conn.close()

    def dense_search(self, embedding: list[float], k: int) -> list[int]:
        """Exact KNN over the dense channel (sqlite-vec brute force). Chunk ids
        nearest-first."""
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT rowid FROM vectors WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (sqlite_vec.serialize_float32(embedding), k),
            ).fetchall()
            return [r["rowid"] for r in rows]
        finally:
            conn.close()

    def sparse_search(self, weights: dict[int, float], k: int) -> list[int]:
        """Learned-sparse channel: sum of weight * query_weight per chunk, best-first."""
        if not weights:
            return []
        conn = self.connect()
        try:
            query_json = json.dumps({str(token_id): w for token_id, w in weights.items()})
            rows = conn.execute(
                """
                SELECT st.chunk_id AS chunk_id, SUM(st.weight * qw.value) AS score
                FROM sparse_terms st
                JOIN json_each(?) AS qw ON CAST(qw.key AS INTEGER) = st.token_id
                GROUP BY st.chunk_id
                ORDER BY score DESC
                LIMIT ?
                """,
                (query_json, k),
            ).fetchall()
            return [r["chunk_id"] for r in rows]
        finally:
            conn.close()

    def bm25_search(self, query: str, k: int) -> list[int]:
        """FTS5 BM25 channel. Each term is individually double-quoted before joining
        with OR — an unescaped query string is FTS5 query syntax, not a literal, and
        raises on stray quotes/operators (query-injection safety)."""
        terms = query.split()
        if not terms:
            return []
        match_expr = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? "
                "ORDER BY bm25(chunks_fts) LIMIT ?",
                (match_expr, k),
            ).fetchall()
            return [r["rowid"] for r in rows]
        finally:
            conn.close()

    def chunk_details(self, chunk_ids: list[int]) -> list[sqlite3.Row]:
        """Resolve chunk ids to citation targets: document + page + heading path."""
        if not chunk_ids:
            return []
        conn = self.connect()
        try:
            placeholders = ",".join("?" for _ in chunk_ids)
            return conn.execute(
                f"""
                SELECT c.id AS chunk_id, c.page, c.heading_path, c.text, m.filename
                FROM chunks c JOIN materials m ON m.id = c.material_id
                WHERE c.id IN ({placeholders})
                """,
                chunk_ids,
            ).fetchall()
        finally:
            conn.close()

    def page_chunks(self, filename: str, page: int) -> list[sqlite3.Row]:
        """Resolve one (filename, page) to its chunks, chunk-id order — the citation
        resource / `get_page` MCP tool's read path."""
        conn = self.connect()
        try:
            return conn.execute(
                """
                SELECT c.id AS chunk_id, c.page, c.heading_path, c.text, m.filename
                FROM chunks c JOIN materials m ON m.id = c.material_id
                WHERE m.filename = ? AND c.page = ?
                ORDER BY c.id
                """,
                (filename, page),
            ).fetchall()
        finally:
            conn.close()

    def add_indexed(
        self,
        filename: str,
        sha256: str,
        pages: int | None,
        chunks: list,
        dense: list[list[float]],
        sparse: list[dict[int, float]],
    ) -> int:
        conn = self.connect()
        try:
            with conn:
                cur = conn.execute(
                    "INSERT INTO materials (filename, sha256, status, pages) VALUES (?, ?, 'indexed', ?)",
                    (filename, sha256, pages),
                )
                material_id = cur.lastrowid
                for chunk, vec, weights in zip(chunks, dense, sparse, strict=True):
                    c_text = chunk.text
                    c_page = chunk.page
                    c_heading_path = chunk.heading_path
                    c_token_count = chunk.token_count

                    cid = conn.execute(
                        "INSERT INTO chunks (material_id, page, heading_path, text, token_count) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (material_id, c_page, c_heading_path, c_text, c_token_count),
                    ).lastrowid
                    conn.execute(
                        "INSERT INTO vectors (rowid, embedding) VALUES (?, ?)",
                        (cid, sqlite_vec.serialize_float32(vec)),
                    )
                    conn.executemany(
                        "INSERT INTO sparse_terms (token_id, chunk_id, weight) VALUES (?, ?, ?)",
                        [(token_id, cid, weight) for token_id, weight in weights.items()],
                    )
                return material_id
        finally:
            conn.close()
