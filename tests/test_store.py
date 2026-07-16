import sqlite3

import pytest
import sqlite_vec

from unilearn.core import store
from unilearn.core.manifest import EMBEDDING_DIM, Manifest, sync_counts


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
    with pytest.raises(RuntimeError, match="newer than this unilearn"):
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


def test_remove_material_leaves_no_rows_in_any_channel(db):
    mid = _add_material(db)
    store.remove_material(db, mid)
    for table in ["materials", "chunks", "sparse_terms", "vectors"]:
        assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert not db.execute(
        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'deadlock'"
    ).fetchall()


def test_find_materials_by_filename_or_sha_prefix(db):
    _add_material(db, "lec1.pdf", "a" * 64)
    _add_material(db, "lec2.pdf", "b" * 64)
    assert len(store.find_materials(db, "lec1.pdf")) == 1
    assert len(store.find_materials(db, "bbbb")) == 1
    assert len(store.find_materials(db, "nope")) == 0
    # LIKE wildcards must not act as wildcards (remove SUBJ '%' would delete anything)
    assert len(store.find_materials(db, "%")) == 0
    assert len(store.find_materials(db, "_" * 8)) == 0


def test_sync_counts(db, tmp_path):
    manifest_path = tmp_path / "manifest.json"
    Manifest.new("T").save(manifest_path)
    _add_material(db, n_chunks=3)
    sync_counts(db, manifest_path)
    m = Manifest.load(manifest_path)
    assert m.counts.materials == 1 and m.counts.chunks == 3
