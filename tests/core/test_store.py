import json
import sqlite3

import pytest
import sqlite_vec

from groundly.core import store
from groundly.core.manifest import EMBEDDING_DIM, Manifest, sync_counts
from groundly.core.store import SQLiteSubjectStore


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "store.db"
    store.create_store(path)
    conn = store.connect(path)
    yield conn
    conn.close()


def _add_material(conn, filename="lec1.pdf", sha="a" * 64, n_chunks=2):
    with conn:
        cur = conn.execute(
            "INSERT INTO materials (filename, sha256, status, pages) VALUES (?, ?, 'indexed', 5)",
            (filename, sha),
        )
        mid = cur.lastrowid
        for i in range(n_chunks):
            c = conn.execute(
                "INSERT INTO chunks (material_id, page, heading_path, text, token_count)"
                " VALUES (?, ?, ?, ?, ?)",
                (mid, i + 1, "Intro > Motivation", f"chunk text {i} about deadlock", 10),
            )
            cid = c.lastrowid
            conn.execute(
                "INSERT INTO vectors (rowid, embedding) VALUES (?, ?)",
                (cid, sqlite_vec.serialize_float32([0.1] * EMBEDDING_DIM)),
            )
            conn.execute(
                "INSERT INTO sparse_terms (token_id, chunk_id, weight) VALUES (7, ?, 0.5)", (cid,)
            )
    return mid


def test_connect_sets_pragmas(db):
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.execute("PRAGMA user_version").fetchone()[0] == store.STORE_USER_VERSION


def test_refuses_newer_schema(tmp_path):
    path = tmp_path / "store.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    with pytest.raises(RuntimeError, match="newer than this groundly"):
        store.connect(path)


def test_duplicate_hash_rejected_by_constraint(db):
    _add_material(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO materials (filename, sha256, status) VALUES ('other.pdf', ?, 'indexed')",
            ("a" * 64,),
        )


def test_fts_search_finds_chunk_text(db):
    _add_material(db)
    hits = db.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'deadlock'").fetchall()
    assert len(hits) == 2


def test_remove_material_leaves_no_rows_in_any_channel(db, tmp_path):
    mid = _add_material(db)
    SQLiteSubjectStore(tmp_path / "store.db").remove_material(mid)
    for table in ["materials", "chunks", "sparse_terms", "vectors"]:
        assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert not db.execute(
        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'deadlock'"
    ).fetchall()


def test_add_indexed_streams_vectors_from_lazy_iterable(db, tmp_path):
    """add_indexed consumes a one-shot (dense, sparse) generator aligned with chunks,
    so a document's vectors need never be materialized as a list at once (findings 3+4+1)."""
    from groundly.ingestion.extract import ChunkData

    store_obj = SQLiteSubjectStore(tmp_path / "store.db")
    chunks = [
        ChunkData("first chunk", "Intro > A", 1, 5),
        ChunkData("second chunk", "Intro > B", 2, 5),
    ]

    def vectors():  # a generator, not a list — proves streaming consumption
        yield [1.0] + [0.0] * (EMBEDDING_DIM - 1), {1: 0.5}
        yield [0.0, 1.0] + [0.0] * (EMBEDDING_DIM - 2), {2: 0.7, 3: 0.3}

    mid = store_obj.add_indexed("lec.pdf", "b" * 64, 7, chunks, vectors())

    assert db.execute("SELECT pages FROM materials WHERE id=?", (mid,)).fetchone()[0] == 7
    assert db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM sparse_terms").fetchone()[0] == 3  # 1 + 2 weights
    assert [r[0] for r in db.execute("SELECT text FROM chunks ORDER BY id")] == [
        "first chunk",
        "second chunk",
    ]


def test_find_materials_by_filename_or_sha_prefix(db, tmp_path):
    _add_material(db, "lec1.pdf", "a" * 64)
    _add_material(db, "lec2.pdf", "b" * 64)
    store_obj = SQLiteSubjectStore(tmp_path / "store.db")
    assert len(store_obj.find_materials("lec1.pdf")) == 1
    assert len(store_obj.find_materials("bbbb")) == 1
    assert len(store_obj.find_materials("nope")) == 0
    # LIKE wildcards must not act as wildcards (remove SUBJ '%' would delete anything)
    assert len(store_obj.find_materials("%")) == 0
    assert len(store_obj.find_materials("_" * 8)) == 0


def test_sync_counts(db, tmp_path):
    manifest_path = tmp_path / "manifest.json"
    Manifest.new("T").save(manifest_path)
    _add_material(db, n_chunks=3)
    sync_counts(db, manifest_path)
    m = Manifest.load(manifest_path)
    assert m.counts.materials == 1 and m.counts.chunks == 3


# --- traces (progress.db) ---------------------------------------------------------


def test_connect_progress_creates_table_on_fresh_file(tmp_path):
    path = tmp_path / "progress.db"
    conn = store.connect_progress(path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0] == 0
    finally:
        conn.close()


def test_connect_progress_upgrades_preexisting_empty_progress_db(tmp_path):
    path = tmp_path / "progress.db"
    store.create_progress(path)  # P1-era empty progress.db, no traces table
    conn = store.connect_progress(path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0] == 0
    finally:
        conn.close()


def test_record_trace_round_trips(tmp_path):
    path = tmp_path / "progress.db"
    conn = store.connect_progress(path)
    try:
        store.record_trace(
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
    conn = store.connect_progress(tmp_path / "progress.db")
    try:
        store.record_trace(conn, kind="search", query="q", outcome="results")
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
    conn = store.connect_progress(tmp_path / "progress.db")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            store.record_trace(conn, **kwargs)
    finally:
        conn.close()


# --- retrieval reads (dense/sparse/bm25/chunk_details) ----------------------------


def _add_chunk(conn, material_id, text, vec, sparse, page=1, heading_path="Intro"):
    cid = conn.execute(
        "INSERT INTO chunks (material_id, page, heading_path, text, token_count) "
        "VALUES (?, ?, ?, ?, ?)",
        (material_id, page, heading_path, text, 10),
    ).lastrowid
    conn.execute(
        "INSERT INTO vectors (rowid, embedding) VALUES (?, ?)",
        (cid, sqlite_vec.serialize_float32(vec)),
    )
    for token_id, weight in sparse.items():
        conn.execute(
            "INSERT INTO sparse_terms (token_id, chunk_id, weight) VALUES (?, ?, ?)",
            (token_id, cid, weight),
        )
    return cid


@pytest.fixture
def ranked(db):
    """Three chunks with orthogonal dense vectors and distinct sparse weights, so
    ranking order is exercised (unlike StubEmbedder's identical vectors)."""
    with db:
        mid = db.execute(
            "INSERT INTO materials (filename, sha256, status, pages) "
            "VALUES ('lec.pdf', ?, 'indexed', 5)",
            ("a" * 64,),
        ).lastrowid
        near = _add_chunk(
            db,
            mid,
            "deadlock needs mutual exclusion",
            [1.0] + [0.0] * (EMBEDDING_DIM - 1),
            {1: 0.9, 2: 0.1},
            page=1,
        )
        far = _add_chunk(
            db,
            mid,
            "semaphores and mutexes for synchronization",
            [0.0, 1.0] + [0.0] * (EMBEDDING_DIM - 2),
            {3: 0.9},
            page=2,
        )
        mid_close = _add_chunk(
            db,
            mid,
            "deadlock deadlock deadlock condition",
            [0.9, 0.1] + [0.0] * (EMBEDDING_DIM - 2),
            {1: 0.4},
            page=3,
        )
    return {"material_id": mid, "near": near, "far": far, "mid": mid_close}


def test_dense_search_orders_by_distance(db, tmp_path, ranked):
    store_obj = SQLiteSubjectStore(tmp_path / "store.db")
    query_vec = [1.0] + [0.0] * (EMBEDDING_DIM - 1)
    ids = store_obj.dense_search(query_vec, k=3)
    assert ids[0] == ranked["near"]
    assert ids[-1] == ranked["far"]


def test_sparse_search_sums_weighted_overlap(db, tmp_path, ranked):
    store_obj = SQLiteSubjectStore(tmp_path / "store.db")
    ids = store_obj.sparse_search({1: 1.0}, k=3)
    assert ids[0] == ranked["near"]  # weight 0.9 beats mid's 0.4
    assert ranked["far"] not in ids  # no overlap on token 1


def test_bm25_search_finds_repeated_term_first(db, tmp_path, ranked):
    store_obj = SQLiteSubjectStore(tmp_path / "store.db")
    ids = store_obj.bm25_search("deadlock", k=3)
    assert ids[0] == ranked["mid"]  # "deadlock" appears 3x
    assert ranked["far"] not in ids


def test_bm25_search_is_safe_against_fts5_syntax_injection(db, tmp_path, ranked):
    store_obj = SQLiteSubjectStore(tmp_path / "store.db")
    # a raw FTS5 MATCH string with this content raises "unterminated string" (verified
    # against sqlite3 directly); the query-safe path must not propagate that.
    ids = store_obj.bm25_search('"; DROP TABLE chunks_fts; --', k=3)
    assert ids == []
    assert db.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == 3


def test_chunk_details_joins_material(db, tmp_path, ranked):
    store_obj = SQLiteSubjectStore(tmp_path / "store.db")
    rows = store_obj.chunk_details([ranked["near"]])
    assert len(rows) == 1
    assert rows[0]["filename"] == "lec.pdf"
    assert rows[0]["page"] == 1
    assert rows[0]["heading_path"] == "Intro"
    assert "mutual exclusion" in rows[0]["text"]


def test_chunk_details_empty_list_returns_empty(db, tmp_path, ranked):
    store_obj = SQLiteSubjectStore(tmp_path / "store.db")
    assert store_obj.chunk_details([]) == []


def test_page_chunks_joins_material_in_chunk_id_order(db, tmp_path, ranked):
    store_obj = SQLiteSubjectStore(tmp_path / "store.db")
    rows = store_obj.page_chunks("lec.pdf", 1)
    assert [r["chunk_id"] for r in rows] == [ranked["near"]]
    assert rows[0]["filename"] == "lec.pdf"
    assert rows[0]["heading_path"] == "Intro"
    assert "mutual exclusion" in rows[0]["text"]


def test_page_chunks_no_match_returns_empty(db, tmp_path, ranked):
    store_obj = SQLiteSubjectStore(tmp_path / "store.db")
    assert store_obj.page_chunks("nope.pdf", 1) == []
    assert store_obj.page_chunks("lec.pdf", 99) == []


def test_removed_material_vanishes_from_all_search_channels(db, tmp_path, ranked):
    store_obj = SQLiteSubjectStore(tmp_path / "store.db")
    store_obj.remove_material(ranked["material_id"])
    query_vec = [1.0] + [0.0] * (EMBEDDING_DIM - 1)
    assert store_obj.dense_search(query_vec, k=10) == []
    assert store_obj.sparse_search({1: 1.0}, k=10) == []
    assert store_obj.bm25_search("deadlock", k=10) == []
    assert store_obj.chunk_details([ranked["near"], ranked["far"], ranked["mid"]]) == []
