"""Configurable ingestion limits: the parent-side file-size guard and the worker's
env-driven image-pixel cap."""

import pytest

from groundly.ingestion.extract import ExtractionFailure, SubprocessExtractor


def test_file_size_guard_rejects_before_spawn(tmp_path):
    big = tmp_path / "huge.pdf"
    big.write_bytes(b"x" * 2048)  # 2 KB
    extractor = SubprocessExtractor(max_file_size_mb=0.001)  # ~1 KB limit
    with pytest.raises(ExtractionFailure, match="file too large"):
        extractor.extract(big)


def test_no_limit_when_unset(tmp_path):
    # max_file_size_mb=None must not trigger the guard (a subprocess failure would be a
    # different error, so a non-"too large" failure proves the guard was skipped)
    f = tmp_path / "x.pdf"
    f.write_bytes(b"x" * 2048)
    extractor = SubprocessExtractor(max_file_size_mb=None, timeout=30)
    with pytest.raises(ExtractionFailure) as exc:
        extractor.extract(f)
    assert "file too large" not in str(exc.value)


def test_worker_honors_env_pixel_cap(monkeypatch, tmp_path):
    from PIL import Image

    from groundly.ingestion import extract_worker

    img_path = tmp_path / "small.png"
    Image.new("RGB", (4, 4)).save(img_path)  # 16 px — well under the default cap
    monkeypatch.setenv("GROUNDLY_MAX_IMAGE_PIXELS", "1")  # now even 16 px is "too large"
    with pytest.raises(SystemExit) as exc:
        extract_worker._first_frame(img_path)
    assert exc.value.code == extract_worker.EXIT_INPUT_TOO_LARGE
