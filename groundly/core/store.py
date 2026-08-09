"""store.db access — the file that travels on export. Schema versioned via PRAGMA
user_version — no migration framework; refuse to open a newer schema than this tool
understands.

Every connection gets WAL + busy_timeout: one-shot CLI runs and host-spawned MCP
processes share the same files (.claude/rules/architecture.md).

progress.db is not served from here — it lives in core/progress.py and is never
exported. Keep its accessors out of this module: this is the file that ships
(.claude/rules/grounding-and-privacy.md — the privacy boundary is a file).
"""

import json
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import sqlite_vec

from groundly.core.manifest import EMBEDDING_DIM

STORE_USER_VERSION = 2

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

_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS decks (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY,
    deck_id INTEGER REFERENCES decks(id) ON DELETE CASCADE,
    type TEXT NOT NULL CHECK (type IN ('flashcard','mcq','short_answer','code','true_false_justify')),
    body TEXT NOT NULL,              -- flashcard front
    answer TEXT NOT NULL,            -- flashcard back / answer key
    distractors TEXT,                -- JSON array; NULL for flashcards
    verify_status TEXT NOT NULL DEFAULT 'verified' CHECK (verify_status IN ('verified')),
    generation_source TEXT NOT NULL CHECK (generation_source IN ('server','host')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_questions_deck ON questions(deck_id);
CREATE TABLE IF NOT EXISTS question_citations (
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    PRIMARY KEY (question_id, chunk_id)
);
"""

# Additive-only migrations, applied in order by connect() when an older store.db is
# opened — no migration framework, just "run this DDL, bump user_version" per step
# (.claude/rules/architecture.md: schema via PRAGMA user_version, no migration
# framework). Keyed by the version each migration upgrades *to*.
_MIGRATIONS: dict[int, str] = {2: _SCHEMA_V2}


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
    # version 0 means "not yet created" — create_store lays down the full schema
    # itself (below), so migrations only apply to an already-initialized store
    # opened with an older-but-nonzero version.
    if version > 0:
        for v in range(version + 1, STORE_USER_VERSION + 1):
            migration = _MIGRATIONS.get(v)
            if migration is None:
                continue
            conn.executescript(migration)
            conn.execute(f"PRAGMA user_version = {v}")
            conn.commit()
    return conn


def create_store(path: Path) -> None:
    conn = connect(path, create=True)
    try:
        conn.executescript(_SCHEMA)
        conn.executescript(_SCHEMA_V2)
        conn.execute(f"PRAGMA user_version = {STORE_USER_VERSION}")
        conn.commit()
    finally:
        conn.close()


def check_deck_name(name: str) -> None:
    """Deck names become file names (exports/<deck>.apkg) — the one host-controlled
    string that reaches the filesystem. Validated at creation AND at export: an
    imported store.db is untrusted and can carry any decks row (security.md import
    trust boundary)."""
    if not name.strip() or "/" in name or "\\" in name or ".." in name:
        raise ValueError(
            f"invalid deck name {name!r} — deck names cannot be empty or contain "
            "path separators or '..'"
        )


class SubjectStore:
    """A subject's store.db: connection lifecycle + all reads/writes for materials,
    chunks, vectors and sparse terms."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        return connect(self.db_path)

    @contextmanager
    def _open(self) -> Iterator[sqlite3.Connection]:
        """Open-and-always-close, the shape every accessor below needs. Distinct from
        the `with conn:` blocks nested inside the writers — that is sqlite3's *commit*
        context (commit on success, rollback on exception) and does not close anything.
        Both are needed; a writer uses them together."""
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    def list_materials(self) -> list[sqlite3.Row]:
        with self._open() as conn:
            return conn.execute(
                """
                SELECT m.id, m.filename, m.sha256, m.status, m.pages, m.error,
                       COUNT(c.id) AS chunk_count
                FROM materials m LEFT JOIN chunks c ON c.material_id = m.id
                GROUP BY m.id ORDER BY m.filename
                """
            ).fetchall()

    def hash_status(self) -> dict[str, str]:
        """sha256 -> status, for hash-skip (indexed) and retry (extraction_failed)."""
        with self._open() as conn:
            return {
                r["sha256"]: r["status"]
                for r in conn.execute("SELECT sha256, status FROM materials")
            }

    def find_materials(self, ident: str) -> list[sqlite3.Row]:
        """Match by exact filename or sha256 prefix (the disambiguator)."""
        with self._open() as conn:
            escaped = ident.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            return conn.execute(
                "SELECT * FROM materials WHERE filename = ? OR sha256 LIKE ? ESCAPE '\\' "
                "ORDER BY filename",
                (ident, escaped + "%"),
            ).fetchall()

    def remove_material(self, material_id: int) -> None:
        """One transaction. FTS syncs via the chunks_ad trigger; sparse_terms via FK
        cascade; vectors (vec0, no FK) deleted explicitly by chunk rowid."""
        with self._open() as conn, conn:
            chunk_ids = [
                r["id"]
                for r in conn.execute("SELECT id FROM chunks WHERE material_id = ?", (material_id,))
            ]
            conn.executemany("DELETE FROM vectors WHERE rowid = ?", [(cid,) for cid in chunk_ids])
            conn.execute("DELETE FROM chunks WHERE material_id = ?", (material_id,))
            conn.execute("DELETE FROM materials WHERE id = ?", (material_id,))
            # question_citations cascade with their chunks (FK ON DELETE CASCADE);
            # a card stripped of every citation must not survive (zero resolvable
            # citations = error, by rule).
            conn.execute(
                "DELETE FROM questions WHERE id NOT IN (SELECT question_id FROM question_citations)"
            )

    def add_extraction_failed(self, filename: str, sha256: str, error: str) -> None:
        with self._open() as conn, conn:
            conn.execute(
                "INSERT INTO materials (filename, sha256, status, error) "
                "VALUES (?, ?, 'extraction_failed', ?)",
                (filename, sha256, error),
            )

    def dense_search(self, embedding: list[float], k: int) -> list[int]:
        """Exact KNN over the dense channel (sqlite-vec brute force). Chunk ids
        nearest-first."""
        with self._open() as conn:
            rows = conn.execute(
                "SELECT rowid FROM vectors WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (sqlite_vec.serialize_float32(embedding), k),
            ).fetchall()
            return [r["rowid"] for r in rows]

    def sparse_search(self, weights: dict[int, float], k: int) -> list[int]:
        """Learned-sparse channel: sum of weight * query_weight per chunk, best-first."""
        if not weights:
            return []
        with self._open() as conn:
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

    def bm25_search(self, query: str, k: int) -> list[int]:
        """FTS5 BM25 channel. Each term is individually double-quoted before joining
        with OR — an unescaped query string is FTS5 query syntax, not a literal, and
        raises on stray quotes/operators (query-injection safety)."""
        terms = query.split()
        if not terms:
            return []
        match_expr = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
        with self._open() as conn:
            rows = conn.execute(
                "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? "
                "ORDER BY bm25(chunks_fts) LIMIT ?",
                (match_expr, k),
            ).fetchall()
            return [r["rowid"] for r in rows]

    def chunk_details(self, chunk_ids: list[int]) -> list[sqlite3.Row]:
        """Resolve chunk ids to citation targets: document + page + heading path."""
        if not chunk_ids:
            return []
        with self._open() as conn:
            placeholders = ",".join("?" for _ in chunk_ids)
            return conn.execute(
                f"""
                SELECT c.id AS chunk_id, c.page, c.heading_path, c.text, m.filename
                FROM chunks c JOIN materials m ON m.id = c.material_id
                WHERE c.id IN ({placeholders})
                """,
                chunk_ids,
            ).fetchall()

    def all_chunks(self) -> list[sqlite3.Row]:
        """Every chunk in the subject, resolved to its citation target — feeds the
        graph batch builder (one input document per chunk)."""
        with self._open() as conn:
            return conn.execute(
                """
                SELECT c.id AS chunk_id, c.page, c.heading_path, c.text, m.filename
                FROM chunks c JOIN materials m ON m.id = c.material_id
                ORDER BY c.id
                """
            ).fetchall()

    def page_chunks(self, filename: str, page: int) -> list[sqlite3.Row]:
        """Resolve one (filename, page) to its chunks, chunk-id order — the citation
        resource / `get_page` MCP tool's read path."""
        with self._open() as conn:
            return conn.execute(
                """
                SELECT c.id AS chunk_id, c.page, c.heading_path, c.text, m.filename
                FROM chunks c JOIN materials m ON m.id = c.material_id
                WHERE m.filename = ? AND c.page = ?
                ORDER BY c.id
                """,
                (filename, page),
            ).fetchall()

    def add_indexed(
        self,
        filename: str,
        sha256: str,
        pages: int | None,
        chunks: list,
        vectors: Iterable[tuple[Sequence[float], dict[int, float]]],
    ) -> int:
        """`vectors` yields one (dense, sparse) pair per chunk, in order. It is consumed
        lazily inside the single per-file transaction, so the caller can stream vectors
        batch-by-batch and never hold the whole document's embeddings at once."""
        with self._open() as conn, conn:
            cur = conn.execute(
                "INSERT INTO materials (filename, sha256, status, pages) VALUES (?, ?, 'indexed', ?)",
                (filename, sha256, pages),
            )
            material_id = cur.lastrowid
            for chunk, (vec, weights) in zip(chunks, vectors, strict=True):
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

    def get_or_create_deck(self, name: str) -> int:
        check_deck_name(name)
        with self._open() as conn, conn:
            conn.execute("INSERT OR IGNORE INTO decks (name) VALUES (?)", (name,))
            row = conn.execute("SELECT id FROM decks WHERE name = ?", (name,)).fetchone()
            return row["id"]

    def add_verified_card(
        self,
        deck_id: int,
        front: str,
        back: str,
        chunk_ids: list[int],
        generation_source: str,
    ) -> int:
        """One transaction: an unresolvable chunk_id violates the citations FK and
        rolls back the whole insert — the FK is the second enforcement of "every
        card cites resolving chunks" (the verifier is the first)."""
        with self._open() as conn, conn:
            cur = conn.execute(
                "INSERT INTO questions (deck_id, type, body, answer, generation_source) "
                "VALUES (?, 'flashcard', ?, ?, ?)",
                (deck_id, front, back, generation_source),
            )
            question_id = cur.lastrowid
            conn.executemany(
                "INSERT INTO question_citations (question_id, chunk_id) VALUES (?, ?)",
                [(question_id, cid) for cid in chunk_ids],
            )
            return question_id

    def deck_cards(self, deck_name: str) -> list[sqlite3.Row]:
        with self._open() as conn:
            return conn.execute(
                """
                SELECT q.id AS question_id, q.body, q.answer,
                       c.id AS chunk_id, m.filename, c.page, c.heading_path
                FROM questions q
                JOIN decks d ON d.id = q.deck_id
                JOIN question_citations qc ON qc.question_id = q.id
                JOIN chunks c ON c.id = qc.chunk_id
                JOIN materials m ON m.id = c.material_id
                WHERE d.name = ?
                ORDER BY q.id, c.id
                """,
                (deck_name,),
            ).fetchall()

    def list_decks(self) -> list[sqlite3.Row]:
        with self._open() as conn:
            return conn.execute(
                """
                SELECT d.name AS name, COUNT(q.id) AS card_count
                FROM decks d LEFT JOIN questions q ON q.deck_id = d.id
                GROUP BY d.id ORDER BY d.name
                """
            ).fetchall()
