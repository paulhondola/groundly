"""Pipeline tests that invoke the real extract worker (bge-m3 tokenizer download on
a cold cache): @pytest.mark.slow, excluded from the default/CI run. Pipeline-logic
tests that stub `extract` live in test_pipeline.py and run everywhere. The real
embedder contract is in test_slow_models.py; UC-01's real-PDF page-attribution
criterion is verified manually per release (no committed PDF fixture)."""

from pathlib import Path

import pytest

from groundly.core.manifest import Manifest
from groundly.core.paths import subject_dir
from groundly.core.store import SQLiteSubjectStore
from groundly.ingestion import pipeline
from groundly.ingestion.extract import SubprocessExtractor


@pytest.mark.slow
def test_index_writes_all_channels_and_copies_materials(subject, course, stub_embedder, connect):
    emb = stub_embedder()
    results = pipeline.index_paths(subject, [course], embedder=emb)
    assert {r.status for r in results} == {"indexed"}
    assert (subject_dir(subject) / "materials" / "notes.txt").exists()
    with connect(subject) as conn:
        assert conn.execute("SELECT COUNT(*) FROM materials").fetchone()[0] == 2
        n_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        assert n_chunks == len(emb.encoded) > 0
        assert conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == n_chunks
        assert conn.execute("SELECT COUNT(*) FROM sparse_terms").fetchone()[0] == n_chunks
    manifest = Manifest.load(subject_dir(subject) / "manifest.json")
    assert manifest.counts.materials == 2 and manifest.counts.chunks == n_chunks


@pytest.mark.slow
def test_md_chunks_carry_heading_path(subject, course, stub_embedder, connect):
    pipeline.index_paths(subject, [course / "readme.md"], embedder=stub_embedder())
    with connect(subject) as conn:
        rows = conn.execute("SELECT heading_path FROM chunks").fetchall()
    assert any(r["heading_path"] and "Deadlock" in r["heading_path"] for r in rows)


@pytest.mark.slow
def test_html_chunks_carry_heading_path(subject, course, stub_embedder, connect):
    html = course / "notes.html"
    html.write_text(
        "<html><body><h1>Deadlock</h1><h2>Conditions</h2>"
        "<p>Four conditions must hold for a deadlock to occur.</p>"
        "</body></html>"
    )
    pipeline.index_paths(subject, [html], embedder=stub_embedder())
    with connect(subject) as conn:
        rows = conn.execute("SELECT heading_path FROM chunks").fetchall()
    assert any(r["heading_path"] and "Deadlock" in r["heading_path"] for r in rows)


@pytest.mark.slow
def test_latex_file_indexes_without_error(subject, course, stub_embedder):
    tex = course / "notes.tex"
    tex.write_text(
        r"\documentclass{article}\begin{document}"
        r"\section{Deadlock}Four conditions must hold for a deadlock to occur."
        r"\end{document}"
    )
    results = pipeline.index_paths(subject, [tex], embedder=stub_embedder())
    assert results[0].status == "indexed"


@pytest.mark.slow
def test_rerun_skips_everything_new_file_embeds_alone(subject, course, stub_embedder):
    pipeline.index_paths(subject, [course], embedder=stub_embedder())
    emb = stub_embedder()
    results = pipeline.index_paths(subject, [course], embedder=emb)
    assert {r.status for r in results} == {"skipped_duplicate"}
    assert emb.encoded == []  # UC-01: no re-embedding

    (course / "new.txt").write_text("Peterson's algorithm ensures mutual exclusion.")
    emb2 = stub_embedder()
    results = pipeline.index_paths(subject, [course], embedder=emb2)
    assert sum(r.status == "indexed" for r in results) == 1
    assert len(emb2.encoded) > 0


@pytest.mark.slow
def test_empty_file_fails_cleanly_then_skips_then_new_hash_indexes(
    subject, course, stub_embedder, connect
):
    empty = course / "empty.txt"
    empty.write_text("   ")
    results = pipeline.index_paths(subject, [empty], embedder=stub_embedder())
    assert results[0].status == "extraction_failed"
    assert "no extractable text" in results[0].detail
    with connect(subject) as conn:
        row = conn.execute("SELECT status, error FROM materials").fetchone()
    assert row["status"] == "extraction_failed"

    # failed is terminal: an unchanged re-run must not re-extract (UC-01 idempotency)
    results = pipeline.index_paths(subject, [empty], embedder=stub_embedder())
    assert results[0].status == "skipped_failed"
    assert "remove to retry" in results[0].detail

    empty.write_text("Now it has real content about semaphores.")  # fixed file = new hash
    results = pipeline.index_paths(subject, [empty], embedder=stub_embedder())
    assert results[0].status == "indexed"


@pytest.mark.slow
def test_embedder_crash_keeps_earlier_file_and_rerun_completes(subject, course, stub_embedder):
    (course / "boom.txt").write_text("TRIGGER embedding failure for this text.")
    emb = stub_embedder(fail_on="TRIGGER")
    results = pipeline.index_paths(subject, [course], embedder=emb)
    by_status = {r.path.name: r.status for r in results}
    assert by_status["boom.txt"] == "error"
    assert by_status["notes.txt"] == "indexed"  # earlier file committed (per-file txn)

    results = pipeline.index_paths(subject, [course], embedder=stub_embedder())
    by_status = {r.path.name: r.status for r in results}
    assert by_status["boom.txt"] == "indexed"  # no terminal row was recorded → retried
    assert by_status["notes.txt"] == "skipped_duplicate"


@pytest.mark.slow
def test_transient_failure_sibling_duplicate_not_misreported(subject, course, stub_embedder):
    """A same-content sibling after a transient embed failure must retry, not be
    reported 'already indexed' with zero rows stored."""
    (course / "a.txt").write_text("TRIGGER text")
    (course / "b.txt").write_text("TRIGGER text")  # same content, same hash
    results = pipeline.index_paths(
        subject, [course / "a.txt", course / "b.txt"], embedder=stub_embedder(fail_on="TRIGGER")
    )
    assert all(r.status == "error" for r in results)  # neither claims success


@pytest.mark.slow
def test_extractor_unavailable_is_transient_then_retries(
    subject, course, monkeypatch, stub_embedder, connect
):
    """A tokenizer/model load failure in the worker is environmental, not a bad document:
    it must be a retryable `error` with no terminal row (unlike no-text), so the next run
    succeeds without the user having to `remove` a wrongly-failed file."""
    from groundly.ingestion.extract import ModelUnavailable

    real_extract = SubprocessExtractor.extract
    calls = {"n": 0}

    def flaky(self, path, ocr_lang=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ModelUnavailable("bge-m3 tokenizer download failed")
        return real_extract(self, path, ocr_lang=ocr_lang)

    monkeypatch.setattr(SubprocessExtractor, "extract", flaky)
    results = pipeline.index_paths(subject, [course / "notes.txt"], embedder=stub_embedder())
    assert results[0].status == "error"  # transient, not extraction_failed
    with connect(subject) as conn:
        assert conn.execute("SELECT COUNT(*) FROM materials").fetchone()[0] == 0  # no terminal row

    results = pipeline.index_paths(subject, [course / "notes.txt"], embedder=stub_embedder())
    assert results[0].status == "indexed"  # environment recovered → retried, no `remove` needed


@pytest.mark.slow
def test_relative_path_indexes(subject, course, tmp_path, monkeypatch, stub_embedder):
    """The extract worker runs with cwd=tempdir; a relative CLI path must still
    resolve (regression: recorded as terminal extraction_failed)."""
    monkeypatch.chdir(tmp_path)
    results = pipeline.index_paths(subject, [Path("course/notes.txt")], embedder=stub_embedder())
    assert results[0].status == "indexed"


@pytest.mark.slow
def test_indexing_orphan_inside_materials_does_not_crash(subject, stub_embedder):
    """UC-01 A4: a Ctrl-C can leave a copied file in materials/ without DB rows;
    re-indexing that exact path must work (regression: shutil.SameFileError)."""
    orphan = subject_dir(subject) / "materials" / "orphan.txt"
    orphan.write_text("Lamport clocks order events without synchronized time.")
    results = pipeline.index_paths(subject, [orphan], embedder=stub_embedder())
    assert results[0].status == "indexed"


@pytest.mark.slow
def test_concurrent_index_race_reports_duplicate_not_crash(
    subject, course, monkeypatch, stub_embedder
):
    """Another process indexing the same content between our hash check and the
    write must surface as a skip, not an unhandled IntegrityError."""
    pipeline.index_paths(subject, [course / "notes.txt"], embedder=stub_embedder())
    monkeypatch.setattr(SQLiteSubjectStore, "hash_status", lambda self: {})  # stale snapshot
    results = pipeline.index_paths(subject, [course / "notes.txt"], embedder=stub_embedder())
    assert results[0].status == "skipped_duplicate"
    assert "concurrent" in results[0].detail
