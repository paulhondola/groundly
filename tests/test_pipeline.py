"""Pipeline tests use a stub embedder. Tests that invoke the real extract worker
(bge-m3 tokenizer download on a cold cache) are @pytest.mark.slow and excluded from
the default/CI run; pipeline-logic tests stub `extract` and run everywhere. The real
embedder contract is in test_slow_models.py; UC-01's real-PDF page-attribution
criterion is verified manually per release (no committed PDF fixture)."""

from pathlib import Path

import pytest

from unilearn.core import store
from unilearn.core.manifest import EMBEDDING_DIM, Manifest
from unilearn.core.paths import subject_dir
from unilearn.core.subject import init_subject
from unilearn.ingestion import pipeline

slow = pytest.mark.slow  # real extract worker: tokenizer download on a cold cache


class StubEmbedder:
    def __init__(self, fail_on: str | None = None):
        self.encoded: list[str] = []
        self.fail_on = fail_on

    def encode(self, texts):
        for t in texts:
            if self.fail_on and self.fail_on in t:
                raise RuntimeError("stub embedder failure")
        self.encoded.extend(texts)
        return [[0.1] * EMBEDDING_DIM for _ in texts], [{1: 0.5} for _ in texts]


@pytest.fixture
def subject(monkeypatch, tmp_path):
    monkeypatch.setenv("UNILEARN_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    init_subject("TEST")
    return "TEST"


@pytest.fixture
def course(tmp_path):
    d = tmp_path / "course"
    d.mkdir()
    (d / "notes.txt").write_text("Deadlock needs mutual exclusion and circular wait to occur.")
    (d / "readme.md").write_text("# Deadlock\n\n## Conditions\n\nFour conditions must hold.\n")
    return d


def _connect(subject):
    return store.connect(subject_dir(subject) / "store.db")


@slow
def test_index_writes_all_channels_and_copies_materials(subject, course):
    emb = StubEmbedder()
    results = pipeline.index_paths(subject, [course], embedder=emb)
    assert {r.status for r in results} == {"indexed"}
    assert (subject_dir(subject) / "materials" / "notes.txt").exists()
    with _connect(subject) as conn:
        assert conn.execute("SELECT COUNT(*) FROM materials").fetchone()[0] == 2
        n_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        assert n_chunks == len(emb.encoded) > 0
        assert conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == n_chunks
        assert conn.execute("SELECT COUNT(*) FROM sparse_terms").fetchone()[0] == n_chunks
    manifest = Manifest.load(subject_dir(subject) / "manifest.json")
    assert manifest.counts.materials == 2 and manifest.counts.chunks == n_chunks


@slow
def test_md_chunks_carry_heading_path(subject, course):
    pipeline.index_paths(subject, [course / "readme.md"], embedder=StubEmbedder())
    with _connect(subject) as conn:
        rows = conn.execute("SELECT heading_path FROM chunks").fetchall()
    assert any(r["heading_path"] and "Deadlock" in r["heading_path"] for r in rows)


@slow
def test_rerun_skips_everything_new_file_embeds_alone(subject, course):
    pipeline.index_paths(subject, [course], embedder=StubEmbedder())
    emb = StubEmbedder()
    results = pipeline.index_paths(subject, [course], embedder=emb)
    assert {r.status for r in results} == {"skipped_duplicate"}
    assert emb.encoded == []  # UC-01: no re-embedding

    (course / "new.txt").write_text("Peterson's algorithm ensures mutual exclusion.")
    emb2 = StubEmbedder()
    results = pipeline.index_paths(subject, [course], embedder=emb2)
    assert sum(r.status == "indexed" for r in results) == 1
    assert len(emb2.encoded) > 0


def test_unsupported_extension_reported_skipped(subject, course):
    (course / "img.png").write_bytes(b"\x89PNG")
    results = pipeline.index_paths(subject, [course / "img.png"], embedder=StubEmbedder())
    assert results[0].status == "skipped_unsupported"
    assert ".png" in results[0].detail


@slow
def test_empty_file_fails_cleanly_then_skips_then_new_hash_indexes(subject, course):
    empty = course / "empty.txt"
    empty.write_text("   ")
    results = pipeline.index_paths(subject, [empty], embedder=StubEmbedder())
    assert results[0].status == "extraction_failed"
    assert "no extractable text" in results[0].detail
    with _connect(subject) as conn:
        row = conn.execute("SELECT status, error FROM materials").fetchone()
    assert row["status"] == "extraction_failed"

    # failed is terminal: an unchanged re-run must not re-extract (UC-01 idempotency)
    results = pipeline.index_paths(subject, [empty], embedder=StubEmbedder())
    assert results[0].status == "skipped_failed"
    assert "remove to retry" in results[0].detail

    empty.write_text("Now it has real content about semaphores.")  # fixed file = new hash
    results = pipeline.index_paths(subject, [empty], embedder=StubEmbedder())
    assert results[0].status == "indexed"


@slow
def test_embedder_crash_keeps_earlier_file_and_rerun_completes(subject, course):
    (course / "boom.txt").write_text("TRIGGER embedding failure for this text.")
    emb = StubEmbedder(fail_on="TRIGGER")
    results = pipeline.index_paths(subject, [course], embedder=emb)
    by_status = {r.path.name: r.status for r in results}
    assert by_status["boom.txt"] == "error"
    assert by_status["notes.txt"] == "indexed"  # earlier file committed (per-file txn)

    results = pipeline.index_paths(subject, [course], embedder=StubEmbedder())
    by_status = {r.path.name: r.status for r in results}
    assert by_status["boom.txt"] == "indexed"  # no terminal row was recorded → retried
    assert by_status["notes.txt"] == "skipped_duplicate"


@slow
def test_transient_failure_sibling_duplicate_not_misreported(subject, course):
    """A same-content sibling after a transient embed failure must retry, not be
    reported 'already indexed' with zero rows stored."""
    (course / "a.txt").write_text("TRIGGER text")
    (course / "b.txt").write_text("TRIGGER text")  # same content, same hash
    results = pipeline.index_paths(
        subject, [course / "a.txt", course / "b.txt"], embedder=StubEmbedder(fail_on="TRIGGER")
    )
    assert all(r.status == "error" for r in results)  # neither claims success


@slow
def test_extractor_unavailable_is_transient_then_retries(subject, course, monkeypatch):
    """A tokenizer/model load failure in the worker is environmental, not a bad document:
    it must be a retryable `error` with no terminal row (unlike no-text), so the next run
    succeeds without the user having to `remove` a wrongly-failed file."""
    from unilearn.ingestion.extract import ModelUnavailable

    real_extract = pipeline.extract
    calls = {"n": 0}

    def flaky(path, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ModelUnavailable("bge-m3 tokenizer download failed")
        return real_extract(path, *args, **kwargs)

    monkeypatch.setattr(pipeline, "extract", flaky)
    results = pipeline.index_paths(subject, [course / "notes.txt"], embedder=StubEmbedder())
    assert results[0].status == "error"  # transient, not extraction_failed
    with _connect(subject) as conn:
        assert conn.execute("SELECT COUNT(*) FROM materials").fetchone()[0] == 0  # no terminal row

    results = pipeline.index_paths(subject, [course / "notes.txt"], embedder=StubEmbedder())
    assert results[0].status == "indexed"  # environment recovered → retried, no `remove` needed


def _stub_extraction():
    from unilearn.ingestion.extract import ChunkData, Extraction

    return Extraction(pages=None, chunks=[ChunkData("stub text", None, None, 2)])


def test_concurrent_failure_race_does_not_abort_run(subject, course, monkeypatch):
    """Another process recording the same failing content between our hash check and
    the failure INSERT must not abort the run (regression: raw IntegrityError)."""
    from unilearn.ingestion.extract import ExtractionFailure

    def always_fail(path, *args, **kwargs):
        raise ExtractionFailure("scanned PDF — not supported")

    monkeypatch.setattr(pipeline, "extract", always_fail)
    pipeline.index_paths(subject, [course / "notes.txt"], embedder=StubEmbedder())
    monkeypatch.setattr(pipeline.store, "hash_status", lambda conn: {})  # stale snapshot
    results = pipeline.index_paths(
        subject, [course / "notes.txt", course / "readme.md"], embedder=StubEmbedder()
    )
    # the race is absorbed and the run continues to the next file
    assert [r.status for r in results] == ["extraction_failed", "extraction_failed"]
    with _connect(subject) as conn:
        assert conn.execute("SELECT COUNT(*) FROM materials").fetchone()[0] == 2


def test_copy_failure_is_transient_and_run_continues(subject, course, monkeypatch):
    """A materials/ copy failure (disk full, permissions) must be a retryable error
    for that file, not abort the whole run (regression: uncaught OSError)."""

    def broken_copy(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(pipeline, "extract", lambda path, *a, **k: _stub_extraction())
    monkeypatch.setattr(pipeline, "_copy_to_materials", broken_copy)
    results = pipeline.index_paths(subject, [course], embedder=StubEmbedder())
    assert [r.status for r in results] == ["error", "error"]  # both files reported, no crash
    with _connect(subject) as conn:
        assert conn.execute("SELECT COUNT(*) FROM materials").fetchone()[0] == 0  # retryable


def test_hardlink_into_materials_does_not_crash(subject, tmp_path, monkeypatch):
    """A hard link shares its target's inode: copying it onto the materials/ original
    raises SameFileError under a resolve()-based check (regression) — samefile treats
    it as already in place."""
    import os

    monkeypatch.setattr(pipeline, "extract", lambda path, *a, **k: _stub_extraction())
    stored = subject_dir(subject) / "materials" / "lec.txt"
    stored.write_text("Lamport clocks order events without synchronized time.")
    src = tmp_path / "lec.txt"
    os.link(stored, src)
    results = pipeline.index_paths(subject, [src], embedder=StubEmbedder())
    assert results[0].status == "indexed"


def test_docling_model_fetch_failure_exits_retryable(monkeypatch, tmp_path):
    """Docling's layout models load before the document is parsed: a fetch failure
    (cold HF cache + offline) must exit EXIT_MODEL_UNAVAILABLE (retryable), never be
    recorded as the PDF's terminal parse failure."""
    from docling.document_converter import DocumentConverter

    from unilearn.ingestion import extract_worker

    def offline_init(self, format):
        raise OSError("couldn't connect to huggingface.co")

    def no_convert(self, path):
        raise AssertionError("document must not be parsed when models are unavailable")

    monkeypatch.setattr(DocumentConverter, "initialize_pipeline", offline_init)
    monkeypatch.setattr(DocumentConverter, "convert", no_convert)
    with pytest.raises(SystemExit) as exc:
        extract_worker._extract_docling(tmp_path / "lec.pdf")
    assert exc.value.code == extract_worker.EXIT_MODEL_UNAVAILABLE


@slow
def test_relative_path_indexes(subject, course, tmp_path, monkeypatch):
    """The extract worker runs with cwd=tempdir; a relative CLI path must still
    resolve (regression: recorded as terminal extraction_failed)."""
    monkeypatch.chdir(tmp_path)
    results = pipeline.index_paths(subject, [Path("course/notes.txt")], embedder=StubEmbedder())
    assert results[0].status == "indexed"


def test_symlink_not_followed(subject, course, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("private key material")
    (course / "link.txt").symlink_to(secret)
    results = pipeline.index_paths(subject, [course / "link.txt"], embedder=StubEmbedder())
    assert results[0].status == "skipped_unsupported"
    assert "symlink" in results[0].detail


@slow
def test_indexing_orphan_inside_materials_does_not_crash(subject):
    """UC-01 A4: a Ctrl-C can leave a copied file in materials/ without DB rows;
    re-indexing that exact path must work (regression: shutil.SameFileError)."""
    orphan = subject_dir(subject) / "materials" / "orphan.txt"
    orphan.write_text("Lamport clocks order events without synchronized time.")
    results = pipeline.index_paths(subject, [orphan], embedder=StubEmbedder())
    assert results[0].status == "indexed"


@slow
def test_concurrent_index_race_reports_duplicate_not_crash(subject, course, monkeypatch):
    """Another process indexing the same content between our hash check and the
    write must surface as a skip, not an unhandled IntegrityError."""
    pipeline.index_paths(subject, [course / "notes.txt"], embedder=StubEmbedder())
    monkeypatch.setattr(pipeline.store, "hash_status", lambda conn: {})  # stale snapshot
    results = pipeline.index_paths(subject, [course / "notes.txt"], embedder=StubEmbedder())
    assert results[0].status == "skipped_duplicate"
    assert "concurrent" in results[0].detail


def test_uninitialized_subject_names_the_fix(monkeypatch, tmp_path, course):
    monkeypatch.setenv("UNILEARN_HOME", str(tmp_path / "home2"))
    with pytest.raises(RuntimeError, match="unilearn init NOPE"):
        pipeline.index_paths("NOPE", [course], embedder=StubEmbedder())
