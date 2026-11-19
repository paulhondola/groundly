"""SubprocessExtractor keeps one worker for a run and replaces it after a crash or timeout,
driven by a stub worker that speaks the protocol without loading any model."""

import sys
from pathlib import Path

import pytest

from groundly.core.config import IngestionSettings
from groundly.ingestion.extract import ExtractionFailure, SubprocessExtractor

STUB = [sys.executable, str(Path(__file__).parent / "stub_worker.py")]


def _ex(timeout=30):
    return SubprocessExtractor(IngestionSettings(timeout_seconds=timeout), argv=STUB)


def _pid(extraction):
    return extraction.chunks[0].text


def test_one_worker_serves_the_whole_run(tmp_path):
    files = [tmp_path / f"f{i}.txt" for i in range(3)]
    for f in files:
        f.write_text("x")
    with _ex() as ex:
        assert len({_pid(ex.extract(f)) for f in files}) == 1


def test_a_crash_fails_only_that_file_and_a_fresh_worker_takes_the_next(tmp_path):
    (tmp_path / "crash.pdf").write_text("x")
    (tmp_path / "ok.txt").write_text("x")
    with _ex() as ex:
        with pytest.raises(ExtractionFailure, match="^parser failed: Segmentation fault"):
            ex.extract(tmp_path / "crash.pdf")
        assert ex.extract(tmp_path / "ok.txt").chunks


def test_a_timeout_names_the_limit_and_the_next_file_still_indexes(tmp_path):
    (tmp_path / "hang.pdf").write_text("x")
    (tmp_path / "ok.txt").write_text("x")
    with _ex(timeout=1) as ex:
        with pytest.raises(ExtractionFailure, match="^extraction timed out after 1s$"):
            ex.extract(tmp_path / "hang.pdf")
        assert ex.extract(tmp_path / "ok.txt").chunks


def test_no_text_keeps_todays_message(tmp_path):
    (tmp_path / "notext.pdf").write_text("x")
    with (
        _ex() as ex,
        pytest.raises(ExtractionFailure, match="^no readable text — OCR found nothing to extract$"),
    ):
        ex.extract(tmp_path / "notext.pdf")


def test_a_worker_that_exits_between_files_is_restarted(tmp_path):
    (tmp_path / "bye.txt").write_text("x")
    (tmp_path / "ok.txt").write_text("x")
    with _ex() as ex:
        ex.extract(tmp_path / "bye.txt")
        assert ex.extract(tmp_path / "ok.txt").chunks


def test_close_leaves_no_worker_running(tmp_path):
    (tmp_path / "ok.txt").write_text("x")
    ex = _ex()
    ex.extract(tmp_path / "ok.txt")
    proc = ex._proc
    ex.close()
    ex.close()
    assert proc.poll() is not None
