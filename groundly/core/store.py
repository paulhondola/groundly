"""store.db access: the file that travels on export. Schema versioned via PRAGMA
user_version with no migrations; a newer schema than this tool knows is refused.
Every connection gets WAL + busy_timeout, since CLI runs and host-spawned MCP processes
share the file. Keep progress.db accessors out of this module: that file never ships.
"""

import json
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import sqlite_vec

from groundly.core.manifest import EMBEDDING_DIM

STORE_USER_VERSION = 1


@dataclass
class TopicWrite:
    """One node of a submitted topic tree. `id` is None for a new topic, or the id of an
    existing one to keep; `chunk_ids` are its sources, best first."""

    id: int | None
    title: str
    summary: str
    chunk_ids: list[int]
    subtopics: list["TopicWrite"] = field(default_factory=list)


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

-- AUTOINCREMENT: a stored citation must never resolve to a different chunk after a removal
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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

-- AUTOINCREMENT: a topic id survives re-submissions and is never reused after a delete.
-- Citations die with their chunk; the topic stays.
CREATE TABLE topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER REFERENCES topics(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL
);
CREATE INDEX idx_topics_parent ON topics(parent_id);

CREATE TABLE topic_citations (
    topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    rank INTEGER NOT NULL,
    PRIMARY KEY (topic_id, chunk_id)
);
CREATE INDEX idx_topic_citations_chunk ON topic_citations(chunk_id);
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
    # Stores from before the 1.0 schema reset carry the dropped `decks` table; their
    # version number would otherwise collide with a future schema's.
    # ponytail: delete this check once no pre-1.0 store or bundle remains.
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'decks'").fetchone():
        conn.close()
        raise RuntimeError(
            f"{path.name} was written before groundly 1.0's schema reset — re-index the "
            "subject from its materials (groundly drop, then index)"
        )
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


class SubjectStore:
    """A subject's store.db: connection lifecycle + all reads/writes for materials,
    chunks, vectors and sparse terms."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        return connect(self.db_path)

    @contextmanager
    def _open(self) -> Iterator[sqlite3.Connection]:
        """Open and always close. A writer's nested `with conn:` is sqlite3's commit context,
        which commits or rolls back but never closes, so writers use both."""
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

    def add_extraction_failed(self, filename: str, sha256: str, error: str) -> None:
        with self._open() as conn, conn:
            conn.execute(
                "INSERT INTO materials (filename, sha256, status, error) "
                "VALUES (?, ?, 'extraction_failed', ?)",
                (filename, sha256, error),
            )

    def dense_search(
        self, embedding: list[float], k: int, material_id: int | None = None
    ) -> list[int]:
        """Exact KNN over the dense channel (sqlite-vec brute force). Chunk ids
        nearest-first.

        Scoped to one material, the vec0 KNN cannot help (it takes no material
        predicate without a metadata column, i.e. a schema change), so that material's
        vectors are ranked by exact L2 distance — vec0's metric — in a plain scan. One
        material's chunks are few."""
        query = sqlite_vec.serialize_float32(embedding)
        with self._open() as conn:
            if material_id is None:
                rows = conn.execute(
                    "SELECT rowid FROM vectors WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                    (query, k),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT rowid FROM vectors "
                    "WHERE rowid IN (SELECT id FROM chunks WHERE material_id = ?) "
                    "ORDER BY vec_distance_l2(embedding, ?) LIMIT ?",
                    (material_id, query, k),
                ).fetchall()
            return [r["rowid"] for r in rows]

    def sparse_search(
        self, weights: dict[int, float], k: int, material_id: int | None = None
    ) -> list[int]:
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
                JOIN chunks c ON c.id = st.chunk_id
                WHERE ? IS NULL OR c.material_id = ?
                GROUP BY st.chunk_id
                ORDER BY score DESC
                LIMIT ?
                """,
                (query_json, material_id, material_id, k),
            ).fetchall()
            return [r["chunk_id"] for r in rows]

    def bm25_search(self, query: str, k: int, material_id: int | None = None) -> list[int]:
        """FTS5 BM25 channel. Each term is individually double-quoted before joining
        with OR — an unescaped query string is FTS5 query syntax, not a literal, and
        raises on stray quotes/operators (query-injection safety)."""
        terms = query.split()
        if not terms:
            return []
        match_expr = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
        with self._open() as conn:
            rows = conn.execute(
                "SELECT chunks_fts.rowid AS rowid FROM chunks_fts "
                "JOIN chunks c ON c.id = chunks_fts.rowid "
                "WHERE chunks_fts MATCH ? AND (? IS NULL OR c.material_id = ?) "
                "ORDER BY bm25(chunks_fts) LIMIT ?",
                (match_expr, material_id, material_id, k),
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

    def page_chunks(self, filename: str, page: int) -> list[sqlite3.Row]:
        """Resolve one (filename, page) to its chunks, chunk-id order — `get_page`'s
        read path for a citation with `#page=N`."""
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

    def material_chunks(self, filename: str) -> list[sqlite3.Row]:
        """Every chunk of one material in page, then chunk-id order — the citation
        resource's read path, and `get_page`'s for a citation without a page."""
        with self._open() as conn:
            return conn.execute(
                """
                SELECT c.id AS chunk_id, c.page, c.heading_path, c.text, m.filename
                FROM chunks c JOIN materials m ON m.id = c.material_id
                WHERE m.filename = ?
                ORDER BY c.page, c.id
                """,
                (filename,),
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

    def topic_ids(self) -> set[int]:
        with self._open() as conn:
            return {r["id"] for r in conn.execute("SELECT id FROM topics")}

    def material_headings(self) -> list[sqlite3.Row]:
        """Each distinct (file, page, heading) in reading order — the outline a topic
        tree is organised around. Chunks without a heading carry no structure."""
        with self._open() as conn:
            return conn.execute(
                """
                SELECT m.filename, c.page, c.heading_path
                FROM chunks c JOIN materials m ON m.id = c.material_id
                WHERE c.heading_path IS NOT NULL AND c.heading_path != ''
                GROUP BY m.filename, c.page, c.heading_path
                ORDER BY m.filename, c.page, MIN(c.id)
                """
            ).fetchall()

    def cited_chunk_counts(self) -> dict[str, int]:
        """filename -> distinct chunks the tree cites from it."""
        with self._open() as conn:
            return {
                r["filename"]: r["n"]
                for r in conn.execute(
                    """
                    SELECT m.filename, COUNT(DISTINCT tc.chunk_id) AS n
                    FROM topic_citations tc
                    JOIN chunks c ON c.id = tc.chunk_id
                    JOIN materials m ON m.id = c.material_id
                    GROUP BY m.filename
                    """
                )
            }

    def tree(self) -> list[dict]:
        """The topic tree in position order, each node's sources in rank order."""
        with self._open() as conn:
            sources: dict[int, list[dict]] = {}
            for r in conn.execute(
                """
                SELECT tc.topic_id, c.id AS chunk_id, c.material_id, m.filename, c.page,
                       c.heading_path, c.text
                FROM topic_citations tc
                JOIN chunks c ON c.id = tc.chunk_id
                JOIN materials m ON m.id = c.material_id
                ORDER BY tc.topic_id, tc.rank
                """
            ):
                sources.setdefault(r["topic_id"], []).append(
                    {
                        "chunk_id": r["chunk_id"],
                        "material_id": r["material_id"],
                        "filename": r["filename"],
                        "page": r["page"],
                        "heading_path": r["heading_path"],
                        "text": r["text"],
                    }
                )
            children: dict[int | None, list[dict]] = {}
            for r in conn.execute(
                "SELECT id, parent_id, title, summary FROM topics ORDER BY position, id"
            ):
                children.setdefault(r["parent_id"], []).append(
                    {
                        "id": r["id"],
                        "title": r["title"],
                        "summary": r["summary"],
                        "sources": sources.get(r["id"], []),
                        "subtopics": [],
                    }
                )
        for siblings in children.values():
            for node in siblings:
                node["subtopics"] = children.get(node["id"], [])
        return children.get(None, [])

    def replace_tree(self, tree: list[TopicWrite]) -> None:
        """One transaction. Every submitted node is written (update by id, else insert)
        before any other topic is deleted, so a subtopic moved out of a dropped parent
        survives the parent's cascade."""
        with self._open() as conn, conn:
            existing = {r["id"] for r in conn.execute("SELECT id FROM topics")}
            kept: set[int] = set()

            def write(nodes: list[TopicWrite], parent_id: int | None) -> None:
                for position, node in enumerate(nodes):
                    if node.id in existing:
                        topic_id = node.id
                        conn.execute(
                            "UPDATE topics SET parent_id = ?, position = ?, title = ?, "
                            "summary = ? WHERE id = ?",
                            (parent_id, position, node.title, node.summary, topic_id),
                        )
                    else:
                        topic_id = conn.execute(
                            "INSERT INTO topics (parent_id, position, title, summary) "
                            "VALUES (?, ?, ?, ?)",
                            (parent_id, position, node.title, node.summary),
                        ).lastrowid
                    kept.add(topic_id)
                    conn.execute("DELETE FROM topic_citations WHERE topic_id = ?", (topic_id,))
                    conn.executemany(
                        "INSERT INTO topic_citations (topic_id, chunk_id, rank) VALUES (?, ?, ?)",
                        [(topic_id, cid, rank) for rank, cid in enumerate(node.chunk_ids)],
                    )
                    write(node.subtopics, topic_id)

            write(tree, None)
            conn.executemany("DELETE FROM topics WHERE id = ?", [(i,) for i in existing - kept])
