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


VERSIONS = {"docling": "2.115.0"}
MACHINE = "MacBookPro18,1 16GB macOS-27.2"


def _legacy(tmp_path):
    import json

    def row(bench, **kw):
        return {"bench": bench, "session": "20261126T094955Z", "sha": "b96dee8",
                "machine": MACHINE, "versions": VERSIONS, "run": 1, "returncode": 0, **kw}  # fmt: skip

    rows = {
        "extract": [row("extract", arm="granite", manifest_version=2,
                        metrics={"wall_s": 2411.4, "load_s": 9.1, "word_f1/image/ro": 0.38})],
        "index": [row("index", arm="docling", timeout_s=300.0, memray=False,
                      metrics={"wall_s": 269.9}, failed=[{"filename": "big.pdf", "error": "boom"}],
                      per_file=[{"file": "ok.pdf", "extract_s": 4.0, "embed_s": 1.5},
                                {"file": "big.pdf", "extract_s": 300.0, "embed_s": 0.0}])],
        "retrieval": [row("retrieval", arm="docling", subject="apd", metrics={"hit_within1@8": 0.9})],
    }  # fmt: skip
    for bench, rs in rows.items():
        (tmp_path / f"{bench}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rs))
    (tmp_path / "pages").mkdir()
    page = {
        "sha256": "aa",
        "page": 1,
        "text": "Logica",
        "ms": 800.0,
        "error": None,
        "capped": False,
    }
    (tmp_path / "pages" / "20261126T094955Z-granite.jsonl").write_text(
        json.dumps(page) + "\n" + json.dumps({"meta": {"load_s": 9.1}}) + "\n"
    )
    return {"version": 2, "pages": [{"sha256": "aa", "page": 1, "kind": "image", "lang": "ro",
                                      "ref": True, "path_hint": "x"}]}  # fmt: skip


def test_import_maps_rows_to_runs_and_files(tmp_path):
    manifest = _legacy(tmp_path)
    con = store.connect(tmp_path / "b.duckdb")
    counts = store.import_jsonl(con, tmp_path, manifest)
    assert counts == {"run": 3, "file": 2, "page": 1, "page_text": 1}
    assert con.execute("select status, error from file order by filename").fetchall() == [
        ("failed", "boom"),
        ("ok", None),
    ]
    assert con.execute("select kind, lang, ref, f1 from page").fetchall() == [
        ("image", "ro", True, None)
    ]
    assert con.execute("select distinct sampler_note from run").fetchall() == [("imported",)]


def test_import_twice_adds_nothing(tmp_path):
    manifest = _legacy(tmp_path)
    con = store.connect(tmp_path / "b.duckdb")
    store.import_jsonl(con, tmp_path, manifest)
    again = store.import_jsonl(con, tmp_path, manifest)
    assert again == {"run": 0, "file": 0, "page": 0, "page_text": 0}
    assert con.execute("select count(*) from run").fetchone() == (3,)
