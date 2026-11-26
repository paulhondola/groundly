import subprocess
import sys

import pytest

duckdb = pytest.importorskip("duckdb")

from bench.common import store

SESSION = {
    "id": "S1",
    "started_at": "2026-11-26 10:00:00",
    "machine": "MacBookPro18,1 16GB macOS-27.2",
    "os": "darwin",
    "git_sha": "abc1234",
    "note": None,
    "packages": {"docling": "2.115.0", "mlx": "0.32.4"},
}
RUN = {"session": "S1", "bench": "extract", "arm": "docling", "run_no": 1, "returncode": 0}
PAGE = {"sha256": "aa", "page": 1, "kind": "image", "lang": "ro", "ref": True, "ms": 812.0}
PAGE_TEXT = {"sha256": "aa", "page": 1, "text": "Logica cablată"}
HOLD = "import duckdb, time; c = duckdb.connect(r'{p}'); print('held', flush=True); time.sleep(30)"


def test_connect_creates_every_table(tmp_path):
    con = store.connect(tmp_path / "b.duckdb")
    names = {
        r[0] for r in con.execute("select table_name from information_schema.tables").fetchall()
    }
    assert {"meta", "session", "session_package", "run", *store.TABLES} <= names


def test_newer_schema_is_refused(tmp_path):
    p = tmp_path / "b.duckdb"
    con = store.connect(p)
    con.execute("update meta set schema_version = 99")
    con.close()
    with pytest.raises(SystemExit, match="schema v99, this bench knows v1"):
        store.connect(p)


def test_write_run_is_one_transaction(tmp_path):
    con = store.connect(tmp_path / "b.duckdb")
    store.ensure_session(con, SESSION)
    store.ensure_session(con, SESSION)
    with pytest.raises(ValueError):
        store.write_run(con, RUN, {"page": [PAGE], "nope": [{}]})
    with pytest.raises(ValueError):
        store.write_run(con, RUN, {"page": [{**PAGE, "bogus": 1}]})
    assert con.execute("select count(*) from run").fetchone() == (0,)
    rid = store.write_run(con, RUN, {"page": [PAGE], "page_text": [PAGE_TEXT]})
    assert con.execute("select run from page").fetchall() == [(rid,)]
    assert con.execute("select text from page_text").fetchall() == [("Logica cablată",)]
    assert con.execute("select count(*) from session_package").fetchone() == (2,)


def test_a_failed_insert_rolls_the_run_back(tmp_path):
    con = store.connect(tmp_path / "b.duckdb")
    store.ensure_session(con, SESSION)
    with pytest.raises(duckdb.Error):
        store.write_run(con, RUN, {"page": [PAGE, {**PAGE, "page": "not a number"}]})
    assert con.execute("select count(*) from run").fetchone() == (0,)
    assert con.execute("select count(*) from page").fetchone() == (0,)


def test_a_second_writer_gets_a_plain_error(tmp_path):
    p = tmp_path / "b.duckdb"
    store.connect(p).close()
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLD.format(p=p)], stdout=subprocess.PIPE, text=True
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(store.StoreBusy, match="in use by another bench run"):
            store.connect(p)
    finally:
        holder.kill()
        holder.wait()
