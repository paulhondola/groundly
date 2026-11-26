import sqlite3
import sys
from pathlib import Path

from bench.index.run import (
    file_seconds,
    ingestion_settings,
    schedule,
    scratch_home,
    stage_seconds,
    store_stats,
    timed_out,
    worker_argv,
)
from groundly.core.store import create_store


def test_schedule_alternates():
    assert schedule(["a", "b"], 2) == [("a", 0), ("b", 0), ("a", 1), ("b", 1)]


def test_scratch_home_holds_only_the_models_link(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    home = scratch_home(models)
    assert [p.name for p in home.iterdir()] == ["models"]
    assert (home / "models").resolve() == models.resolve()


def test_worker_argv_plain_and_memray(tmp_path):
    assert worker_argv("x", None) == [sys.executable, "-m", "bench.stacks.x", "--serve"]
    assert worker_argv("x", tmp_path / "w.bin") == [
        sys.executable, "-m", "memray", "run", "-q", "-o", str(tmp_path / "w.bin"),
        "-m", "bench.stacks.x", "--serve",
    ]  # fmt: skip


def test_store_stats_reads_chunks_and_failures(tmp_path):
    db = tmp_path / "s" / "store.db"
    db.parent.mkdir()
    create_store(db)
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO materials (id, filename, sha256, status) VALUES (1, 'ok.pdf', 'a', 'indexed')"
    )
    con.execute(
        "INSERT INTO materials (id, filename, sha256, status, error) "
        "VALUES (2, 'big.pdf', 'b', 'extraction_failed', 'extraction timed out after 300s')"
    )
    con.executemany(
        "INSERT INTO chunks (material_id, text) VALUES (1, ?)", [("x",), ("y",), ("z",)]
    )
    con.commit()
    con.close()
    stats = store_stats(tmp_path, "s")
    assert stats["chunks"] == 3 and stats["materials"] == 2
    assert stats["failed"] == [{"filename": "big.pdf", "error": "extraction timed out after 300s"}]


def test_stage_seconds_counts_a_failed_extraction_as_extract_time():
    marks = {
        "ok.pdf": {"extracting": 10.0, "embedding": 14.0, "end": 15.5},
        "big.pdf": {"extracting": 15.5, "end": 315.5},
    }
    assert stage_seconds(marks) == (304.0, 1.5)


def test_file_seconds_keeps_each_file_for_graphs():
    marks = {
        Path("m/ok.pdf"): {"extracting": 10.0, "embedding": 14.0, "end": 15.5},
        Path("m/big.pdf"): {"extracting": 15.5, "end": 315.5},
        Path("m/skipped.pdf"): {"end": 316.0},
    }
    assert file_seconds(marks) == [
        {"file": "ok.pdf", "extract_s": 4.0, "embed_s": 1.5},
        {"file": "big.pdf", "extract_s": 300.0, "embed_s": 0.0},
    ]


def test_product_arm_runs_the_shipped_worker():
    assert worker_argv("product", None)[1:] == [
        "-m",
        "groundly.ingestion.extract_worker",
        "--serve",
    ]


def test_timeout_overrides_only_the_timeout(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path))  # no config file: shipped defaults
    s = ingestion_settings(3600)
    assert s.timeout_seconds == 3600
    assert s.max_pages == ingestion_settings(None).max_pages
    assert ingestion_settings(None).timeout_seconds == 300


def test_index_row_counts_timed_out_files():
    failed = [
        {"filename": "a.pdf", "error": "extraction timed out — no page finished within 300s"},
        {"filename": "b.pdf", "error": "parser failed: boom"},
    ]
    assert timed_out(failed) == 1


def test_memray_artifacts_record_peak_heap_and_flamegraph(tmp_path):
    import subprocess

    from bench.index.run import memray_artifacts

    script = tmp_path / "m.py"
    script.write_text("b = bytearray(30 << 20)\n")
    capture = tmp_path / "w.bin"
    subprocess.run(
        [sys.executable, "-m", "memray", "run", "-q", "-o", str(capture), str(script)], check=True
    )
    rows = memray_artifacts({"worker": capture, "parent": tmp_path / "missing.bin"})
    heap, html = rows
    assert heap["kind"] == "memray" and heap["process"] == "worker" and heap["peak_heap_mb"] >= 30
    assert html["kind"] == "memray_html" and Path(html["path"]).is_file()
