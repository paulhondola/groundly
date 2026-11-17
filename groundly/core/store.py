"""store.db access: the file that travels on export. Schema versioned via PRAGMA
user_version with no migrations; a newer schema than this tool knows is refused.
Every connection gets WAL + busy_timeout, since CLI runs and host-spawned MCP processes
share the file. Keep progress.db accessors out of this module: that file never ships.
"""

import json
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import sqlite_vec

from groundly.core.manifest import EMBEDDING_DIM

STORE_USER_VERSION = 2

# What a stored topic tree may cost: the gate refuses a submission over these and import
# refuses a bundle over them, so a hostile bundle cannot make `tree()` outgrow memory.
MAX_TREE_NODES = 1000
MAX_SOURCES = 5  # citations per topic
MAX_TITLE_CHARS = 200
MAX_SUMMARY_CHARS = 500
MAX_TREE_SOURCE_BYTES = 64 * 1024 * 1024  # chunk text `tree()` loads across all citations
MAX_BANK_SOURCE_BYTES = 64 * 1024 * 1024  # chunk text `questions()` loads across the bank

# What a stored question may cost: the question gate and import both refuse anything over these.
MAX_STEM_CHARS = 500
MAX_CODE_CHARS = 2000
MAX_OPTION_CHARS = 300
MAX_REFERENCE_CHARS = 1000
MAX_QUESTION_CITATIONS = 5
MAX_QUESTIONS_PER_TOPIC = 200
# kind -> (fewest options, most options, fewest correct)
QUESTION_SHAPES = {"single": (2, 6, 1), "multiple": (3, 8, 2)}


def text_key(text: str) -> str:
    """How two stems or two options are compared for sameness, by the gate and by import."""
    return text.strip().casefold()


@dataclass
class TopicWrite:
    """One node of a submitted topic tree. `id` is None for a new topic, or the id of an
    existing one to keep; `chunk_ids` are its sources, best first."""

    id: int | None
    title: str
    summary: str
    chunk_ids: list[int]
    subtopics: list["TopicWrite"] = field(default_factory=list)


@dataclass
class QuestionWrite:
    """One question the gate has passed. `options` are (text, correct) in the order shown,
    empty for an open question; `reference_answer` is set for an open question only."""

    kind: str
    stem: str
    code: str | None
    reference_answer: str | None
    options: list[tuple[str, bool]]
    chunk_ids: list[int]


class SchemaUpgradeError(RuntimeError):
    """A version-1 store could not gain the question tables: it already holds an object of
    the same name, which groundly never writes there. Nothing was changed."""


class StaleTopicError(Exception):
    """A submitted tree keeps a topic id that no longer exists: another submit dropped it
    after the caller read the ids. Raised before anything is written."""


_BASE_SCHEMA = f"""
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

# Kept apart from the base schema so a version-1 store gains exactly these objects on open.
_QUESTION_SCHEMA = """
-- AUTOINCREMENT: a recorded answer must never come to mean a different question.
CREATE TABLE questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('single', 'multiple', 'open')),
    stem TEXT NOT NULL,
    code TEXT,
    reference_answer TEXT
);
CREATE INDEX idx_questions_topic ON questions(topic_id);

CREATE TABLE question_options (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    text TEXT NOT NULL,
    correct INTEGER NOT NULL CHECK (correct IN (0, 1))
);
CREATE INDEX idx_question_options_question ON question_options(question_id);

CREATE TABLE question_citations (
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    PRIMARY KEY (question_id, chunk_id)
);
CREATE INDEX idx_question_citations_chunk ON question_citations(chunk_id);

-- Every stored question cites a chunk that resolves: one that loses its last citation goes too.
CREATE TRIGGER question_citations_ad AFTER DELETE ON question_citations
WHEN NOT EXISTS (SELECT 1 FROM question_citations WHERE question_id = old.question_id)
BEGIN
    DELETE FROM questions WHERE id = old.question_id;
END;
"""

_SCHEMA = _BASE_SCHEMA + _QUESTION_SCHEMA


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
    if version == 1:
        try:
            _upgrade(conn)
        except sqlite3.OperationalError as exc:
            conn.close()
            if "already exists" not in str(exc):
                raise  # a lock or I/O failure keeps its own message
            raise SchemaUpgradeError(
                f"{path.name} could not be upgraded to schema {STORE_USER_VERSION} — {exc}"
            ) from exc
    return conn


def _upgrade(conn: sqlite3.Connection) -> None:
    """Give a version-1 store the question tables, touching no existing row. The version is
    read again under the write lock: another process may have upgraded while this one waited."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        if conn.execute("PRAGMA user_version").fetchone()[0] == 1:
            for statement in _statements(_QUESTION_SCHEMA):
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {STORE_USER_VERSION}")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _statements(script: str) -> Iterator[str]:
    """Split a schema script without leaving its transaction, which `executescript` would."""
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            yield buffer.strip()
            buffer = ""


def programs(conn: sqlite3.Connection) -> set[tuple[str, str, str]]:
    """The store's views and triggers: the schema objects that run SQL on a later read or write."""
    rows = conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE type IN ('view', 'trigger')"
    )
    return {tuple(r) for r in rows}


# Objects sqlite-vec and FTS5 create for their virtual tables: their DDL belongs to the
# extension, so a store is checked for their presence, not their exact SQL.
_EXTENSION_OBJECTS = ("vectors_", "chunks_fts_", "sqlite_autoindex_vectors_")


def schema(conn: sqlite3.Connection) -> dict[tuple[str, str], tuple[str, str | None]]:
    """Every table and index: (type, name) -> (table it belongs to, its SQL)."""
    rows = conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE type IN ('table', 'index')"
    )
    return {(r[0], r[1]): (r[2], r[3]) for r in rows}


def schema_problem(conn: sqlite3.Connection) -> str | None:
    """Why this store's tables and indexes differ from a fresh store's, or None. An object
    groundly declares must match it exactly; an extension's object must exist under its
    shipped name. A missing table is left to the checks that need it, but an index on a
    present table must be there: without one, a check's scan grows with the square."""
    shipped = shipped_schema()
    found = schema(conn)
    for key, (table, sql) in found.items():
        if key not in shipped:
            return f"{key[0]} {key[1]!r}"
        if table != shipped[key][0] or (
            not key[1].startswith(_EXTENSION_OBJECTS) and sql != shipped[key][1]
        ):
            return f"a changed {key[0]} {key[1]!r}"
    tables = {name for kind, name in found if kind == "table"}
    for key, (table, _) in shipped.items():
        if key[0] == "index" and table in tables and key not in found:
            return f"a missing index {key[1]!r}"
    return None


def _fresh() -> sqlite3.Connection:
    """An in-memory store built from the shipped schema: what a store is compared against."""
    conn = connect(Path(":memory:"), create=True)
    conn.executescript(_SCHEMA)
    return conn


def shipped_schema() -> dict[tuple[str, str], tuple[str, str | None]]:
    with closing(_fresh()) as conn:
        return schema(conn)


def shipped_programs() -> set[tuple[str, str, str]]:
    with closing(_fresh()) as conn:
        return programs(conn)


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
                cid = conn.execute(
                    "INSERT INTO chunks (material_id, page, heading_path, text, token_count) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (material_id, chunk.page, chunk.heading_path, chunk.text, chunk.token_count),
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

    def material_filename(self, material_id: int) -> str | None:
        """The stored basename of an indexed material, or None. Untrusted on an imported
        store: callers resolve it against the materials dir before touching the file."""
        with self._open() as conn:
            row = conn.execute(
                "SELECT filename FROM materials WHERE id = ? AND status = 'indexed'",
                (material_id,),
            ).fetchone()
        return row["filename"] if row else None

    def topic_ids(self) -> set[int]:
        with self._open() as conn:
            return {r["id"] for r in conn.execute("SELECT id FROM topics")}

    def leaf_topic_ids(self) -> set[int]:
        """Topics with no subtopics: the only ones that hold questions."""
        with self._open() as conn:
            return {
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM topics WHERE id NOT IN "
                    "(SELECT parent_id FROM topics WHERE parent_id IS NOT NULL)"
                )
            }

    def question_ids_by_topic(self) -> dict[int, list[int]]:
        """Stored question ids per topic id, in id order; a topic with none is absent."""
        with self._open() as conn:
            out: dict[int, list[int]] = {}
            for r in conn.execute("SELECT topic_id, id FROM questions ORDER BY id"):
                out.setdefault(r["topic_id"], []).append(r["id"])
            return out

    def question_ids(self) -> set[int]:
        with self._open() as conn:
            return {r["id"] for r in conn.execute("SELECT id FROM questions")}

    def question_stems(self, topic_id: int) -> list[str]:
        with self._open() as conn:
            return [
                r["stem"]
                for r in conn.execute(
                    "SELECT stem FROM questions WHERE topic_id = ? ORDER BY id", (topic_id,)
                )
            ]

    def add_questions(self, topic_id: int, questions: list[QuestionWrite]) -> list[int]:
        """One transaction; returns the new ids in the order given. The topic is checked
        again under the write lock: a re-map may have given it subtopics or removed it since
        the gate looked, and then StaleTopicError is raised and nothing is written."""
        ids: list[int] = []
        with self._open() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute(
                "SELECT 1 FROM topics WHERE id = ? AND NOT EXISTS "
                "(SELECT 1 FROM topics c WHERE c.parent_id = topics.id)",
                (topic_id,),
            ).fetchone():
                raise StaleTopicError(f"topic {topic_id} is no longer a topic without subtopics")
            for q in questions:
                question_id = conn.execute(
                    "INSERT INTO questions (topic_id, kind, stem, code, reference_answer) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (topic_id, q.kind, q.stem, q.code, q.reference_answer),
                ).lastrowid
                conn.executemany(
                    "INSERT INTO question_options (question_id, position, text, correct) "
                    "VALUES (?, ?, ?, ?)",
                    [
                        (question_id, position, text, int(correct))
                        for position, (text, correct) in enumerate(q.options)
                    ],
                )
                conn.executemany(
                    "INSERT INTO question_citations (question_id, chunk_id) VALUES (?, ?)",
                    [(question_id, cid) for cid in q.chunk_ids],
                )
                ids.append(question_id)
        return ids

    def remove_questions(self, ids: list[int]) -> list[int]:
        """Delete these questions, all or none: returns the ids that do not exist, and
        deletes only when that list is empty."""
        with self._open() as conn, conn:
            known = {r["id"] for r in conn.execute("SELECT id FROM questions")}
            unknown = [i for i in ids if i not in known]
            if not unknown:
                conn.executemany("DELETE FROM questions WHERE id = ?", [(i,) for i in ids])
            return unknown

    def questions(
        self,
        *,
        ids: list[int] | None = None,
        topic_ids: list[int] | None = None,
        with_sources: bool = True,
    ) -> list[dict]:
        """Stored questions in id order, each with its options in position order and its
        sources in the shape `tree()` gives a node (left empty with `with_sources=False`,
        which skips loading chunk text). With neither filter, every question."""
        where, params = "", []
        for column, values in (("q.id", ids), ("q.topic_id", topic_ids)):
            if values is not None:
                where = f"WHERE {column} IN ({','.join('?' for _ in values)})"
                params = list(values)
        with self._open() as conn:
            out = {
                r["id"]: {**dict(r), "options": [], "sources": []}
                for r in conn.execute(
                    f"SELECT q.id, q.topic_id, q.kind, q.stem, q.code, q.reference_answer "
                    f"FROM questions q {where} ORDER BY q.id",
                    params,
                )
            }
            for r in conn.execute(
                f"SELECT o.question_id, o.id, o.text, o.correct FROM question_options o "
                f"JOIN questions q ON q.id = o.question_id {where} ORDER BY o.position, o.id",
                params,
            ):
                out[r["question_id"]]["options"].append(
                    {"id": r["id"], "text": r["text"], "correct": bool(r["correct"])}
                )
            if not with_sources:
                return list(out.values())
            for r in conn.execute(
                f"""
                SELECT qc.question_id, c.id AS chunk_id, c.material_id, m.filename, c.page,
                       c.heading_path, c.text
                FROM question_citations qc
                JOIN questions q ON q.id = qc.question_id
                JOIN chunks c ON c.id = qc.chunk_id
                JOIN materials m ON m.id = c.material_id
                {where} ORDER BY c.id
                """,
                params,
            ):
                source = dict(r)
                out[source.pop("question_id")]["sources"].append(source)
        return list(out.values())

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

    def tree(self, with_sources: bool = True) -> list[dict]:
        """The topic tree in position order, each node's sources in rank order. With
        `with_sources=False` every node's sources are empty and no chunk text is loaded."""
        with self._open() as conn:
            sources: dict[int, list[dict]] = {}
            for r in (
                conn.execute(
                    """
                SELECT tc.topic_id, c.id AS chunk_id, c.material_id, m.filename, c.page,
                       c.heading_path, c.text
                FROM topic_citations tc
                JOIN chunks c ON c.id = tc.chunk_id
                JOIN materials m ON m.id = c.material_id
                ORDER BY tc.topic_id, tc.rank
                """
                )
                if with_sources
                else ()
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
        survives the parent's cascade. A kept id that is gone, or a topic holding questions
        that the tree leaves out or subdivides, raises StaleTopicError and nothing is written:
        the gate checked both before its slow search, and another writer may have acted since."""
        with self._open() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = {r["id"] for r in conn.execute("SELECT id FROM topics")}
            kept: set[int] = set()

            def write(nodes: list[TopicWrite], parent_id: int | None) -> None:
                for position, node in enumerate(nodes):
                    if node.id is not None and node.id not in existing:
                        raise StaleTopicError(f"topic {node.id} no longer exists")
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
            stranded = conn.execute(
                "SELECT DISTINCT q.topic_id FROM questions q LEFT JOIN topics t ON t.id = q.topic_id "
                "WHERE t.id IS NULL OR q.topic_id NOT IN ({}) OR EXISTS "
                "(SELECT 1 FROM topics c WHERE c.parent_id = q.topic_id)".format(
                    ",".join("?" for _ in kept) or "NULL"
                ),
                list(kept),
            ).fetchall()
            if stranded:
                raise StaleTopicError(
                    f"topic {stranded[0][0]} holds questions and this tree leaves it out or "
                    "gives it subtopics"
                )
            conn.executemany("DELETE FROM topics WHERE id = ?", [(i,) for i in existing - kept])
