import pytest

from bench.common.compare import table
from bench.common.results import append


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


ROW = row("main", 240)


def test_append_writes_one_line_per_row(tmp_path):
    append("index", ROW, results_dir=tmp_path)
    append("index", ROW, results_dir=tmp_path)
    assert len((tmp_path / "index.jsonl").read_text().splitlines()) == 2


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
