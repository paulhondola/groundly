import statistics

import pytest

duckdb = pytest.importorskip("duckdb")

from bench.common import store
from bench.report import run as report

SECRET = "zz-course-text-never-exported"
SESSION = {"id": "S", "started_at": None, "machine": "M", "os": None, "git_sha": None, "note": None}


def _power(watts: float, seconds: int) -> list[dict]:
    return [{"t_s": t / 2, "combined_mw": watts, "cpu_mw": watts / 2, "gpu_active": 0.5}
            for t in range(2 * seconds + 1)]  # fmt: skip


def _proc() -> list[dict]:
    return [{"t_s": t / 10, "rss_mb": 100.0 + t, "footprint_mb": 120.0 + t, "cpu_s": t / 20}
            for t in range(20)]  # fmt: skip


@pytest.fixture
def con(tmp_path):
    c = store.connect(tmp_path / "b.duckdb")
    store.ensure_session(c, SESSION)
    base = {"session": "S", "returncode": 0, "wall_s": 10.0, "run_no": 1, "idle_combined_mw": 400.0}
    page = {"sha256": "ab", "page": 1, "kind": "image", "lang": "ro", "ms": 900.0, "f1": 0.8,
            "precision": 0.9, "recall": 0.7, "scorer_version": 1}  # fmt: skip
    store.write_run(c, {**base, "bench": "extract", "arm": "docling", "items": 1, "load_s": 2.0}, {
        "page": [page], "page_text": [{"sha256": "ab", "page": 1, "text": SECRET}],
        "sample_proc": _proc(), "sample_power": _power(1000, 10),
    })  # fmt: skip
    store.write_run(c, {**base, "bench": "index", "arm": "docling", "subject": "apd", "items": 40}, {
        "file": [{"filename": "a.pdf", "extract_s": 6.0, "embed_s": 2.0, "status": "ok"}],
        "sample_proc": _proc(), "sample_power": _power(1000, 10),
    })  # fmt: skip
    store.write_run(c, {**base, "bench": "index", "arm": "docling", "subject": "apd", "memray": True}, {
        "artifact": [{"kind": "memray", "path": "/x.bin", "process": "worker", "peak_heap_mb": 512.0}],
    })  # fmt: skip
    hits = [{"query_id": "0", "latency_ms": 700.0, "hit_within1_at8": True}]
    store.write_run(
        c, {**base, "bench": "retrieval", "arm": "docling", "subject": "apd"}, {"query": hits}
    )
    ids = [
        store.write_run(
            c,
            {
                **base,
                "bench": "embed",
                "arm": arm,
                "subject": "apd",
                "items": 100,
                "embed_s": 4.0,
                "rerank_s": 2.0,
                "pairs": 20,
                "device": arm,
            },
            {"query": hits, "sample_proc": _proc()},
        )
        for arm in ("mlx", "torch")
    ]
    store.insert(c, "agreement", [{"run_a": ids[0], "run_b": ids[1], "item_kind": "chunk",
                                   "item_id": "1", "dense_cos": 0.9999}])  # fmt: skip
    return c


def test_every_result_exports(con, tmp_path):
    out = report.export(con, tmp_path / "export")
    names = sorted(p.name for p in out)
    assert names == sorted(f"R{i}.{ext}" for i in range(1, 16) for ext in ("parquet", "csv"))


def test_no_export_carries_page_text(con, tmp_path):
    out = report.export(con, tmp_path / "export")
    for p in out:
        if p.suffix == ".parquet":
            cols = [r[0] for r in duckdb.sql(f"describe select * from '{p}'").fetchall()]
            assert "text" not in cols, p.name
        else:
            assert SECRET not in p.read_text(), p.name


def test_bootstrap_ci_brackets_the_mean():
    values = [0.2, 0.4, 0.5, 0.6, 0.9, 0.7, 0.3]
    lo, hi = report.bootstrap_ci(values)
    assert lo < statistics.fmean(values) < hi
    assert report.bootstrap_ci([0.5]) == (0.5, 0.5)


def test_energy_subtracts_idle(con, tmp_path):
    report.export(con, tmp_path / "export")
    rows = duckdb.sql(
        f"select bench, energy_j from '{tmp_path / 'export' / 'R6.parquet'}' where bench = 'extract'"
    ).fetchall()
    assert rows == [("extract", pytest.approx(6.0))]  # (1000 − 400) mW × 10 s


def test_figures_draw_one_png_per_result(con, tmp_path):
    pytest.importorskip("matplotlib")
    report.export(con, tmp_path / "export")
    pngs = report.figures(tmp_path / "export")
    assert sorted(p.name for p in pngs) == sorted(f"R{i}.png" for i in range(1, 16))


def test_a_later_memray_session_keeps_the_plain_index_run(con, tmp_path):
    store.ensure_session(con, {**SESSION, "id": "T"})  # sorts after "S", like a later --memray run
    store.write_run(con, {"session": "T", "bench": "index", "arm": "docling", "subject": "apd",
                          "run_no": 1, "returncode": 0, "wall_s": 12.0, "memray": True}, {})  # fmt: skip
    report.export(con, tmp_path / "export")
    r8 = duckdb.sql(f"select wall_s from '{tmp_path / 'export' / 'R8.parquet'}'").fetchall()
    r15 = duckdb.sql(f"select count(*) from '{tmp_path / 'export' / 'R15.parquet'}'").fetchall()
    assert r8 == [(10.0,)]  # the plain run from session S
    assert r15 == [(0,)]  # session T's memray run has no artifacts; S's memray run is superseded
