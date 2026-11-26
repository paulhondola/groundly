import pytest

pytest.importorskip("duckdb")  # bench group only; CI installs `--extra dev`

from bench.common.compare import table


def row(arm, wall, returncode=0, session="S2", machine="M", manifest_version=None):
    return {
        "bench": "index",
        "session": session,
        "arm": arm,
        "sha": "abc",
        "machine": machine,
        "versions": {},
        "ocr_lang": "en",
        "manifest_version": manifest_version,
        "run": 1,
        "returncode": returncode,
        "metrics": {"wall_s": float(wall)},
    }


def rows_for(arm, walls, **kw):
    return [row(arm, w, **kw) for w in walls]


def test_table_reports_median_spread_and_ratio():
    rows = rows_for("main", [240, 236, 238]) + rows_for("router", [200, 204, 198])
    out = table(rows, baseline="main", metric="wall_s", gate=0.85)
    assert "238.0" in out and "200.0" in out and "0.84" in out and "PASS" in out


def test_gate_fails_above_ratio():
    rows = rows_for("main", [100]) + rows_for("router", [95])
    assert "FAIL" in table(rows, "main", "wall_s", 0.85)


def test_failed_runs_are_dropped_and_counted():
    rows = rows_for("main", [240, 238]) + [row("main", 10, returncode=1)]
    assert "1 failed run dropped" in table(rows, "main", "wall_s", None)


def test_only_the_latest_session_holding_the_baseline_is_compared():
    old = rows_for("router", [150], session="S1")
    new = rows_for("main", [240]) + rows_for("router", [200])  # session "S2"
    out = table(old + new, "main", "wall_s", None)
    assert "150" not in out


def test_mixed_machines_or_manifests_refuse():
    rows = rows_for("main", [240]) + rows_for("router", [200], machine="Other")
    with pytest.raises(ValueError, match="machine"):
        table(rows, "main", "wall_s", None)
    rows = rows_for("main", [240]) + rows_for("router", [200], manifest_version="2")
    with pytest.raises(ValueError, match="manifest_version"):
        table(rows, "main", "wall_s", None)


def test_compare_reads_the_store_and_skips_memray(tmp_path):
    pytest.importorskip("duckdb")
    from bench.common import store
    from bench.common.compare import rows_from_store

    con = store.connect(tmp_path / "b.duckdb")
    store.ensure_session(
        con,
        {"id": "S", "started_at": None, "machine": "M", "os": None, "git_sha": None, "note": None},
    )
    for arm, wall, memray in (("main", 240, False), ("main", 999, True), ("router", 200, False)):
        run = {"session": "S", "bench": "index", "arm": arm, "wall_s": wall, "returncode": 0}
        store.write_run(con, {**run, "memray": memray}, {})
    hits = [{"query_id": "0", "hit_within1_at8": True}, {"query_id": "1", "hit_within1_at8": False}]
    store.write_run(
        con, {"session": "S", "bench": "retrieval", "arm": "main", "returncode": 0}, {"query": hits}
    )
    out = table(rows_from_store(con, "index"), "main", "wall_s", 0.85)
    assert "999" not in out and "PASS" in out
    assert rows_from_store(con, "retrieval")[0]["metrics"]["hit_within1@8"] == 0.5
