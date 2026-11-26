import json
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


def test_a_failed_import_leaves_no_half_imported_session(tmp_path):
    manifest = _legacy(tmp_path)
    broken = {"session": "20261126T094955Z", "bench": "index", "arm": "x",
              "per_file": [{"file": "a.pdf"}]}  # fmt: skip
    with (tmp_path / "index.jsonl").open("a") as f:
        f.write(json.dumps(broken) + "\n")
    con = store.connect(tmp_path / "b.duckdb")
    with pytest.raises(KeyError):
        store.import_jsonl(con, tmp_path, manifest)
    assert con.execute("select count(*) from session").fetchone() == (0,)
    assert con.execute("select count(*) from run").fetchone() == (0,)


def test_imported_ocr_lang_is_part_of_the_arm(tmp_path):
    manifest = _legacy(tmp_path)
    rows = [json.loads(line) for line in (tmp_path / "extract.jsonl").read_text().splitlines()]
    rows[0]["ocr_lang"] = "ro"
    (tmp_path / "extract.jsonl").write_text(json.dumps(rows[0]) + "\n")
    con = store.connect(tmp_path / "b.duckdb")
    store.import_jsonl(con, tmp_path, manifest)
    assert con.execute("select arm from run where bench = 'extract'").fetchone() == ("granite:ro",)


def test_file_rows_use_the_pipeline_outcome():
    per_file = [
        {"file": "ok.pdf", "extract_s": 1.0, "embed_s": 1.0, "status": "indexed", "error": None},
        {"file": "oom.pdf", "extract_s": 1.0, "embed_s": 9.0, "status": "error",
         "error": "embedding failed: out of memory"},
        {"file": "bad.pdf", "extract_s": 2.0, "embed_s": 0.0, "status": "extraction_failed",
         "error": "parser failed: boom"},
    ]  # fmt: skip
    failed = [{"filename": "bad.pdf", "error": "parser failed: boom"}]
    got = {r["filename"]: (r["status"], r["error"]) for r in store.file_rows(per_file, failed)}
    assert got == {
        "ok.pdf": ("ok", None),
        "oom.pdf": ("error", "embedding failed: out of memory"),
        "bad.pdf": ("failed", "parser failed: boom"),
    }


def test_import_twice_adds_nothing(tmp_path):
    manifest = _legacy(tmp_path)
    con = store.connect(tmp_path / "b.duckdb")
    store.import_jsonl(con, tmp_path, manifest)
    again = store.import_jsonl(con, tmp_path, manifest)
    assert again == {"run": 0, "file": 0, "page": 0, "page_text": 0}
    assert con.execute("select count(*) from run").fetchone() == (3,)


_SESSION = {
    "id": "S",
    "started_at": None,
    "machine": "M",
    "os": None,
    "git_sha": None,
    "note": None,
}
_RUN = {"session": "S", "bench": "extract", "arm": "a", "returncode": 0, "wall_s": 1.0}


def _hold(p, seconds):
    code = HOLD.format(p=p).replace("time.sleep(30)", f"time.sleep({seconds})")
    holder = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    return holder


def test_each_run_records_its_own_sha_and_packages(tmp_path):
    p = tmp_path / "b.duckdb"
    store.save({**SESSION, "id": "S"}, _RUN, {}, path=p)
    store.save({**SESSION, "id": "S", "git_sha": "def5678", "packages": {"mlx": "0.33"}}, _RUN, {},
               path=p)  # fmt: skip
    con = store.connect(p)
    assert con.execute("select git_sha, packages from run order by id").fetchall() == [
        ("abc1234", json.dumps(SESSION["packages"])),
        ("def5678", '{"mlx": "0.33"}'),
    ]
    assert con.execute("select git_sha from session").fetchall() == [("abc1234",)]


def test_an_older_store_gains_the_run_provenance_columns(tmp_path):
    p = tmp_path / "b.duckdb"
    con = store.connect(p)
    con.execute("alter table run drop column git_sha")
    con.close()
    con = store.connect(p)
    assert "git_sha" in [c[0] for c in con.execute("describe run").fetchall()]


def test_read_only_refuses_a_missing_store(tmp_path):
    with pytest.raises(SystemExit, match="does not exist yet"):
        store.connect(tmp_path / "none.duckdb", read_only=True)
    assert not (tmp_path / "none.duckdb").exists()


def test_a_read_only_reader_waits_for_a_writer(tmp_path):
    p = tmp_path / "b.duckdb"
    store.connect(p).close()
    holder = _hold(p, 1.5)
    try:
        store.connect_waiting(p, wait_s=15, read_only=True).close()
    finally:
        holder.kill()
        holder.wait()


def test_save_waits_for_a_busy_store(tmp_path):
    p = tmp_path / "b.duckdb"
    store.connect(p).close()
    holder = _hold(p, 1.5)
    try:
        assert store.save(_SESSION, _RUN, {}, path=p, wait_s=15) > 0
    finally:
        holder.kill()
        holder.wait()


def test_save_gives_up_with_a_one_line_error(tmp_path):
    p = tmp_path / "b.duckdb"
    store.connect(p).close()
    holder = _hold(p, 30)
    try:
        with pytest.raises(SystemExit, match="in use by another bench run"):
            store.save(_SESSION, _RUN, {}, path=p, wait_s=1)
    finally:
        holder.kill()
        holder.wait()
