"""Pipeline tests use a stub embedder (no model download) and .txt/.md fixtures
(Docling's layout models are only needed for PDFs). The real embedder contract is
@pytest.mark.slow in test_slow_models.py; UC-01's real-PDF page-attribution criterion
is verified manually per release (no committed PDF fixture)."""

import pytest

from unilearn.core import store
from unilearn.core.manifest import EMBEDDING_DIM, Manifest
from unilearn.core.paths import subject_dir
from unilearn.core.subject import init_subject
from unilearn.ingestion import pipeline

pytestmark = pytest.mark.slow


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


def test_md_chunks_carry_heading_path(subject, course):
    pipeline.index_paths(subject, [course / "readme.md"], embedder=StubEmbedder())
    with _connect(subject) as conn:
        rows = conn.execute("SELECT heading_path FROM chunks").fetchall()
    assert any(r["heading_path"] and "Deadlock" in r["heading_path"] for r in rows)


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


def test_transient_failure_sibling_duplicate_not_misreported(subject, course):
    """A same-content sibling after a transient embed failure must retry, not be
    reported 'already indexed' with zero rows stored."""
    (course / "a.txt").write_text("TRIGGER text")
    (course / "b.txt").write_text("TRIGGER text")  # same content, same hash
    results = pipeline.index_paths(
        subject, [course / "a.txt", course / "b.txt"], embedder=StubEmbedder(fail_on="TRIGGER")
    )
    assert all(r.status == "error" for r in results)  # neither claims success


def test_symlink_not_followed(subject, course, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("private key material")
    (course / "link.txt").symlink_to(secret)
    results = pipeline.index_paths(subject, [course / "link.txt"], embedder=StubEmbedder())
    assert results[0].status == "skipped_unsupported"
    assert "symlink" in results[0].detail


def test_indexing_orphan_inside_materials_does_not_crash(subject):
    """UC-01 A4: a Ctrl-C can leave a copied file in materials/ without DB rows;
    re-indexing that exact path must work (regression: shutil.SameFileError)."""
    orphan = subject_dir(subject) / "materials" / "orphan.txt"
    orphan.write_text("Lamport clocks order events without synchronized time.")
    results = pipeline.index_paths(subject, [orphan], embedder=StubEmbedder())
    assert results[0].status == "indexed"


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
