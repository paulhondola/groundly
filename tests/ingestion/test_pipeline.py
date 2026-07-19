"""Fast pipeline-logic tests using classes and interfaces directly, avoiding heavy global patching.
Real-worker tests (bge-m3 tokenizer download on a cold cache) live in test_pipeline_slow.py and
are @pytest.mark.slow, excluded from the default/CI run."""

import pytest

from groundly.core.subject import Subject
from groundly.ingestion import pipeline
from groundly.ingestion.pipeline import IngestionPipeline
from groundly.ingestion.results import Status


def test_unsupported_extension_reported_skipped(subject, course, stub_embedder, stub_extractor):
    (course / "archive.xyz").write_bytes(b"\x00\x01")
    subj = Subject(subject)
    pipe = IngestionPipeline(subject=subj, extractor=stub_extractor(), embedder=stub_embedder())
    results = pipe.run([course / "archive.xyz"])
    assert results[0].status == Status.SKIPPED_UNSUPPORTED
    assert ".xyz" in results[0].detail


def test_docling_suffixes_are_a_subset_of_supported_suffixes():
    from groundly.ingestion.formats import DOCLING_FORMATS, DOCLING_SUFFIXES, SUPPORTED_SUFFIXES

    assert DOCLING_SUFFIXES <= SUPPORTED_SUFFIXES
    assert all(suffix.startswith(".") and suffix == suffix.lower() for suffix in DOCLING_FORMATS)


def test_new_plain_text_format_indexes(subject, course, stub_embedder, stub_extractor):
    subj = Subject(subject)
    pipe = IngestionPipeline(subject=subj, extractor=stub_extractor(), embedder=stub_embedder())
    (course / "config.yaml").write_text("course: Deadlock Theory\nweek: 3\n")
    results = pipe.run([course / "config.yaml"])
    assert results[0].status == Status.INDEXED


def test_ocr_lang_reaches_extract(subject, course, stub_embedder, stub_extractor):
    subj = Subject(subject)
    extractor = stub_extractor()
    pipe = IngestionPipeline(subject=subj, extractor=extractor, embedder=stub_embedder())
    pipe.run([course / "notes.txt"], ocr_lang="ro")
    assert extractor.seen_langs == ["ro"]


def test_concurrent_failure_race_does_not_abort_run(
    subject, course, monkeypatch, stub_embedder, connect
):
    """Another process recording the same failing content between our hash check and
    the failure INSERT must not abort the run (regression: raw IntegrityError)."""
    from groundly.ingestion.extract import ExtractionFailure

    class AlwaysFailExtractor:
        def extract(self, path, ocr_lang=None):
            raise ExtractionFailure("no readable text — OCR found nothing to extract")

    subj = Subject(subject)
    pipe = IngestionPipeline(
        subject=subj, extractor=AlwaysFailExtractor(), embedder=stub_embedder()
    )
    pipe.run([course / "notes.txt"])

    # Simulate a stale snapshot by resetting the known hash list to empty
    monkeypatch.setattr(pipe.store, "hash_status", lambda: {})
    results = pipe.run([course / "notes.txt", course / "readme.md"])

    # the race is absorbed and the run continues to the next file
    assert [r.status for r in results] == [Status.EXTRACTION_FAILED, Status.EXTRACTION_FAILED]
    with connect(subject) as conn:
        assert conn.execute("SELECT COUNT(*) FROM materials").fetchone()[0] == 2


def test_copy_failure_is_transient_and_run_continues(
    subject, course, monkeypatch, stub_embedder, stub_extraction, connect
):
    """A materials/ copy failure (disk full, permissions) must be a retryable error
    for that file, not abort the whole run (regression: uncaught OSError)."""

    def broken_copy(*args, **kwargs):
        raise OSError("disk full")

    class NormalExtractor:
        def extract(self, path, ocr_lang=None):
            return stub_extraction()

    monkeypatch.setattr(pipeline, "_copy_to_materials", broken_copy)
    subj = Subject(subject)
    pipe = IngestionPipeline(subject=subj, extractor=NormalExtractor(), embedder=stub_embedder())
    results = pipe.run([course])
    assert [r.status for r in results] == [
        Status.ERROR,
        Status.ERROR,
    ]  # both files reported, no crash
    with connect(subject) as conn:
        assert conn.execute("SELECT COUNT(*) FROM materials").fetchone()[0] == 0  # retryable


def test_hardlink_into_materials_does_not_crash(subject, tmp_path, stub_embedder, stub_extraction):
    """A hard link shares its target's inode: copying it onto the materials/ original
    raises SameFileError under a resolve()-based check (regression) — samefile treats
    it as already in place."""
    import os

    class NormalExtractor:
        def extract(self, path, ocr_lang=None):
            return stub_extraction()

    subj = Subject(subject)
    stored = subj.materials_dir / "lec.txt"
    stored.write_text("Lamport clocks order events without synchronized time.")
    src = tmp_path / "lec.txt"
    os.link(stored, src)

    pipe = IngestionPipeline(subject=subj, extractor=NormalExtractor(), embedder=stub_embedder())
    results = pipe.run([src])
    assert results[0].status == Status.INDEXED


def test_symlink_not_followed(subject, course, tmp_path, stub_embedder, stub_extractor):
    secret = tmp_path / "secret.txt"
    secret.write_text("private key material")
    (course / "link.txt").symlink_to(secret)

    subj = Subject(subject)
    pipe = IngestionPipeline(subject=subj, extractor=stub_extractor(), embedder=stub_embedder())
    results = pipe.run([course / "link.txt"])
    assert results[0].status == Status.SKIPPED_UNSUPPORTED
    assert "symlink" in results[0].detail


def test_default_deny_list_prunes_junk_dirs(subject, course, stub_embedder, stub_extractor):
    (course / ".venv" / "lib").mkdir(parents=True)
    (course / ".venv" / "lib" / "foo.py").write_text("junk")
    (course / "dist").mkdir()
    (course / "dist" / "bundle.js").write_text("junk")

    subj = Subject(subject)
    pipe = IngestionPipeline(subject=subj, extractor=stub_extractor(), embedder=stub_embedder())
    results = pipe.run([course])
    indexed_names = {r.path.name for r in results if r.status == Status.INDEXED}
    assert indexed_names == {"notes.txt", "readme.md"}


def test_hidden_dir_and_file_skipped_by_default(subject, course, stub_embedder, stub_extractor):
    (course / ".hidden_dir").mkdir()
    (course / ".hidden_dir" / "secret.txt").write_text("junk")
    (course / ".DS_Store").write_text("junk")

    subj = Subject(subject)
    pipe = IngestionPipeline(subject=subj, extractor=stub_extractor(), embedder=stub_embedder())
    results = pipe.run([course])
    indexed_names = {r.path.name for r in results if r.status == Status.INDEXED}
    assert indexed_names == {"notes.txt", "readme.md"}


def test_groundlyignore_prunes_dir_and_skips_glob_match(
    subject, course, stub_embedder, stub_extractor
):
    (course / "scratch").mkdir()
    (course / "scratch" / "junk.txt").write_text("junk")
    (course / "draft.tmp.md").write_text("junk")
    (course / ".groundlyignore").write_text("scratch\n*.tmp.md\n")

    subj = Subject(subject)
    pipe = IngestionPipeline(subject=subj, extractor=stub_extractor(), embedder=stub_embedder())
    results = pipe.run([course])
    indexed_names = {r.path.name for r in results if r.status == Status.INDEXED}
    assert indexed_names == {"notes.txt", "readme.md"}


def test_groundlyignore_comments_and_blank_lines_are_inert(
    subject, course, stub_embedder, stub_extractor
):
    (course / ".groundlyignore").write_text("# a comment\n\n   \n")

    subj = Subject(subject)
    pipe = IngestionPipeline(subject=subj, extractor=stub_extractor(), embedder=stub_embedder())
    results = pipe.run([course])
    indexed_names = {r.path.name for r in results if r.status == Status.INDEXED}
    assert indexed_names == {"notes.txt", "readme.md"}


def test_explicit_path_wins_over_ignore_pattern(subject, course, stub_embedder, stub_extractor):
    (course / "draft.tmp.md").write_text("Deadlock notes worth indexing.")
    (course / ".groundlyignore").write_text("*.tmp.md\n")

    subj = Subject(subject)
    pipe = IngestionPipeline(subject=subj, extractor=stub_extractor(), embedder=stub_embedder())
    results = pipe.run([course / "draft.tmp.md"])
    assert results[0].status == Status.INDEXED


def test_on_discovered_and_terminal_events_match_file_count(
    subject, course, stub_embedder, stub_extractor
):
    discovered: list[int] = []
    terminal_events: list[str] = []

    def on_discovered(total):
        discovered.append(total)

    def on_event(path, stage):
        if stage in set(Status):
            terminal_events.append(stage)

    subj = Subject(subject)
    pipe = IngestionPipeline(
        subject=subj,
        extractor=stub_extractor(),
        embedder=stub_embedder(),
        on_event=on_event,
        on_discovered=on_discovered,
    )
    pipe.run([course])

    assert discovered == [2]  # notes.txt, readme.md
    assert len(terminal_events) == discovered[0]


def test_uninitialized_subject_names_the_fix(
    monkeypatch, tmp_path, course, stub_embedder, stub_extractor
):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home2"))
    subj = Subject("NOPE")
    pipe = IngestionPipeline(subject=subj, extractor=stub_extractor(), embedder=stub_embedder())
    with pytest.raises(RuntimeError, match="groundly init NOPE"):
        pipe.run([course])
