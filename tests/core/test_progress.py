import sqlite3

from groundly.core import progress


def test_create_progress_makes_an_empty_wal_database(tmp_path):
    path = tmp_path / "progress.db"
    progress.create_progress(path)
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0
    finally:
        conn.close()
