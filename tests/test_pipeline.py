"""Fast pipeline-logic tests: stubbed `extract`, no real extract worker. Real-worker
tests (bge-m3 tokenizer download on a cold cache) live in test_pipeline_slow.py and
are @pytest.mark.slow, excluded from the default/CI run. The real embedder contract
is in test_slow_models.py; UC-01's real-PDF page-attribution criterion is verified
manually per release (no committed PDF fixture)."""

import pytest

from groundly.core.paths import subject_dir
from groundly.ingestion import pipeline


def test_unsupported_extension_reported_skipped(subject, course, stub_embedder):
    (course / "img.png").write_bytes(b"\x89PNG")
    results = pipeline.index_paths(subject, [course / "img.png"], embedder=stub_embedder())
    assert results[0].status == "skipped_unsupported"
    assert ".png" in results[0].detail


def test_docling_suffixes_are_a_subset_of_supported_suffixes():
    from groundly.ingestion.formats import DOCLING_FORMATS, DOCLING_SUFFIXES, SUPPORTED_SUFFIXES

    assert DOCLING_SUFFIXES <= SUPPORTED_SUFFIXES
    assert all(suffix.startswith(".") and suffix == suffix.lower() for suffix in DOCLING_FORMATS)


def test_new_plain_text_format_indexes(
    subject, course, monkeypatch, stub_embedder, stub_extraction
):
    monkeypatch.setattr(pipeline, "extract", lambda path, *a, **k: stub_extraction())
    (course / "config.yaml").write_text("course: Deadlock Theory\nweek: 3\n")
    results = pipeline.index_paths(subject, [course / "config.yaml"], embedder=stub_embedder())
    assert results[0].status == "indexed"


def test_concurrent_failure_race_does_not_abort_run(
    subject, course, monkeypatch, stub_embedder, connect
):
    """Another process recording the same failing content between our hash check and
    the failure INSERT must not abort the run (regression: raw IntegrityError)."""
    from groundly.ingestion.extract import ExtractionFailure

    def always_fail(path, *args, **kwargs):
        raise ExtractionFailure("scanned PDF — not supported")

    monkeypatch.setattr(pipeline, "extract", always_fail)
    pipeline.index_paths(subject, [course / "notes.txt"], embedder=stub_embedder())
    monkeypatch.setattr(pipeline.store, "hash_status", lambda conn: {})  # stale snapshot
    results = pipeline.index_paths(
        subject, [course / "notes.txt", course / "readme.md"], embedder=stub_embedder()
    )
    # the race is absorbed and the run continues to the next file
    assert [r.status for r in results] == ["extraction_failed", "extraction_failed"]
    with connect(subject) as conn:
        assert conn.execute("SELECT COUNT(*) FROM materials").fetchone()[0] == 2


def test_copy_failure_is_transient_and_run_continues(
    subject, course, monkeypatch, stub_embedder, stub_extraction, connect
):
    """A materials/ copy failure (disk full, permissions) must be a retryable error
    for that file, not abort the whole run (regression: uncaught OSError)."""

    def broken_copy(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(pipeline, "extract", lambda path, *a, **k: stub_extraction())
    monkeypatch.setattr(pipeline, "_copy_to_materials", broken_copy)
    results = pipeline.index_paths(subject, [course], embedder=stub_embedder())
    assert [r.status for r in results] == ["error", "error"]  # both files reported, no crash
    with connect(subject) as conn:
        assert conn.execute("SELECT COUNT(*) FROM materials").fetchone()[0] == 0  # retryable


def test_hardlink_into_materials_does_not_crash(
    subject, tmp_path, monkeypatch, stub_embedder, stub_extraction
):
    """A hard link shares its target's inode: copying it onto the materials/ original
    raises SameFileError under a resolve()-based check (regression) — samefile treats
    it as already in place."""
    import os

    monkeypatch.setattr(pipeline, "extract", lambda path, *a, **k: stub_extraction())
    stored = subject_dir(subject) / "materials" / "lec.txt"
    stored.write_text("Lamport clocks order events without synchronized time.")
    src = tmp_path / "lec.txt"
    os.link(stored, src)
    results = pipeline.index_paths(subject, [src], embedder=stub_embedder())
    assert results[0].status == "indexed"


def test_symlink_not_followed(subject, course, tmp_path, stub_embedder):
    secret = tmp_path / "secret.txt"
    secret.write_text("private key material")
    (course / "link.txt").symlink_to(secret)
    results = pipeline.index_paths(subject, [course / "link.txt"], embedder=stub_embedder())
    assert results[0].status == "skipped_unsupported"
    assert "symlink" in results[0].detail


def test_uninitialized_subject_names_the_fix(monkeypatch, tmp_path, course, stub_embedder):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home2"))
    with pytest.raises(RuntimeError, match="groundly init NOPE"):
        pipeline.index_paths("NOPE", [course], embedder=stub_embedder())
