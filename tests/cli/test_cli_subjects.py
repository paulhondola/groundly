"""CLI: grammar pinned by the P1 surface design; init/list/remove exercised for real
against a temp GROUNDLY_HOME. Heavy index logic is covered in test_pipeline.py; the
CLI index test stubs the pipeline entry point."""

import logging

import pytest
from typer.testing import CliRunner

from groundly.cli import app
from groundly.core.paths import subject_dir
from groundly.ingestion import pipeline
from groundly.ingestion.results import FileResult, Status
from tests.conftest import init_subject

runner = CliRunner()


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


@pytest.fixture(autouse=True)
def _reset_logging():
    """`--debug` tests attach a real handler to the process-global root logger —
    reset it after each test so it never leaks into an unrelated test."""
    import groundly.core.logs as logs_mod

    root = logging.getLogger()
    before_handlers = list(root.handlers)
    before_levels = {name: logging.getLogger(name).level for name in logs_mod._LOGGER_NAMES}
    yield
    for handler in list(root.handlers):
        if handler not in before_handlers:
            root.removeHandler(handler)
    for name, level in before_levels.items():
        logging.getLogger(name).setLevel(level)
    logs_mod._configured = False


def test_no_args_shows_help():
    assert "Usage" in runner.invoke(app, []).output


def test_list_all_subjects():
    init_subject("PDSS")
    init_subject("ML")
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "PDSS" in result.output and "ML" in result.output


def test_list_all_shows_pages_and_ocr():
    from groundly.core.store import SubjectStore
    from groundly.core.subject import Subject
    from groundly.ingestion.extract import ChunkData

    init_subject("PDSS")
    subj = Subject("PDSS")
    SubjectStore(subj.store_db_path).add_indexed(
        "lec.pdf", "a" * 64, 5, [ChunkData("t", None, 1, 1)], iter([([0.1] * 1024, {1: 0.5})])
    )
    manifest = subj.load_manifest()
    manifest.ocr.lang = ["ro"]
    subj.save_manifest(manifest)
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0, result.output
    header, row = (line for line in result.output.splitlines() if "pages" in line or "PDSS" in line)
    assert "pages" in header and "ocr" in header
    assert " 5 " in row and "ro" in row


def test_list_subject_shows_sha_prefix():
    sdir = _seed_two(sha_a="ab12cd34" + "0" * 56)
    result = runner.invoke(app, ["list", "PDSS"])
    assert result.exit_code == 0, result.output
    assert "ab12cd34" in result.output
    assert "ab12cd34" + "0" * 56 not in result.output
    assert sdir.exists()


def test_list_unknown_subject_names_the_fix():
    result = runner.invoke(app, ["list", "NOPE"])
    assert result.exit_code == 1
    assert "groundly index NOPE" in result.output


def test_index_reports_results(monkeypatch, tmp_path):
    init_subject("PDSS")
    f = tmp_path / "lec.txt"
    f.write_text("content")

    def fake_index_paths(
        subject, paths, embedder=None, on_event=None, on_discovered=None, ocr_lang=None, **_
    ):
        return [FileResult(f, Status.INDEXED, chunks=3)]

    monkeypatch.setattr(pipeline, "index_paths", fake_index_paths)
    result = runner.invoke(app, ["index", "PDSS", str(f)])
    assert result.exit_code == 0, result.output
    assert "1 indexed" in result.output and "(3 chunks)" in result.output


def test_index_ocr_lang_set_reuse_mismatch(monkeypatch, tmp_path):
    """--ocr-lang: first use persists into the manifest; same value reuses; a
    different value is refused (re-index migration, decision 15)."""
    from groundly.core.manifest import Manifest

    init_subject("PDSS")
    f = tmp_path / "lec.txt"
    f.write_text("content")
    seen = []

    def fake_index_paths(
        subject, paths, embedder=None, on_event=None, on_discovered=None, ocr_lang=None, **_
    ):
        seen.append(ocr_lang)
        return [FileResult(f, Status.INDEXED, chunks=1)]

    monkeypatch.setattr(pipeline, "index_paths", fake_index_paths)
    manifest_path = subject_dir("PDSS") / "manifest.json"

    # set: persisted into manifest.json and passed to the pipeline
    assert runner.invoke(app, ["index", "PDSS", str(f), "--ocr-lang", "ro"]).exit_code == 0
    assert Manifest.load(manifest_path).ocr.lang == ["ro"]

    # reuse: same flag ok; no flag falls back to the recorded value
    assert runner.invoke(app, ["index", "PDSS", str(f), "--ocr-lang", "ro"]).exit_code == 0
    assert runner.invoke(app, ["index", "PDSS", str(f)]).exit_code == 0
    assert seen == ["ro", "ro", "ro"]

    # mismatch with indexed materials: refused, manifest untouched
    manifest = Manifest.load(manifest_path)
    manifest.counts.materials = 1
    manifest.save(manifest_path)
    result = runner.invoke(app, ["index", "PDSS", str(f), "--ocr-lang", "en"])
    assert result.exit_code == 1
    assert "already set to 'ro'" in result.output and "re-index" in result.output
    assert Manifest.load(manifest_path).ocr.lang == ["ro"]

    # mismatch with nothing indexed: allowed — recovers from a mistyped lang,
    # which stores no rows (every extraction exits model-unavailable)
    manifest = Manifest.load(manifest_path)
    manifest.counts.materials = 0
    manifest.save(manifest_path)
    assert runner.invoke(app, ["index", "PDSS", str(f), "--ocr-lang", "en"]).exit_code == 0
    assert Manifest.load(manifest_path).ocr.lang == ["en"]


@pytest.mark.parametrize("args", [["list", "../evil"], ["remove", "../evil", "x.pdf", "-y"]])
def test_bad_subject_name_fails_cleanly_not_traceback(args):
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "invalid subject name" in result.output


def test_remove_unknown_material():
    init_subject("PDSS")
    result = runner.invoke(app, ["remove", "PDSS", "ghost.pdf", "-y"])
    assert result.exit_code == 1
    assert "no material" in result.output


def test_remove_deletes_rows_and_file(home):
    from groundly.core import store

    init_subject("PDSS")
    sdir = subject_dir("PDSS")
    (sdir / "materials" / "lec.txt").write_text("x")
    conn = store.connect(sdir / "store.db")
    with conn:
        conn.execute(
            "INSERT INTO materials (filename, sha256, status) VALUES ('lec.txt', ?, 'indexed')",
            ("c" * 64,),
        )
    conn.close()

    result = runner.invoke(app, ["remove", "PDSS", "lec.txt", "--yes"])
    assert result.exit_code == 0, result.output
    assert not (sdir / "materials" / "lec.txt").exists()
    conn = store.connect(sdir / "store.db")
    assert conn.execute("SELECT COUNT(*) FROM materials").fetchone()[0] == 0
    conn.close()


def test_list_missing_store_db_names_cause_not_traceback():
    init_subject("PDSS")
    sdir = subject_dir("PDSS")
    (sdir / "store.db").unlink()
    result = runner.invoke(app, ["list", "PDSS"])
    assert result.exit_code == 1
    assert "store.db is missing" in result.output
    assert not (sdir / "store.db").exists()  # regression: connect() created an empty db


def test_list_empty_store_db_names_cause_not_traceback():
    init_subject("PDSS")
    sdir = subject_dir("PDSS")
    (sdir / "store.db").unlink()
    (sdir / "store.db").touch()  # exists but schema-less (e.g. interrupted init)
    result = runner.invoke(app, ["list", "PDSS"])
    assert result.exit_code == 1
    assert "corrupt or incomplete" in result.output


def test_list_all_skips_corrupt_manifest_with_warning():
    init_subject("PDSS")
    init_subject("ML")
    (subject_dir("PDSS") / "manifest.json").write_text("{ truncated")
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0  # one damaged subject must not take down the listing
    assert "ML" in result.output
    assert "PDSS" in result.output and "corrupt" in result.output


def test_remove_failed_row_keeps_indexed_siblings_file(home):
    """A failed row records the original filename with no collision suffix — removing
    it must not delete a same-named indexed material's stored file (citation target)."""
    from groundly.core import store

    init_subject("PDSS")
    sdir = subject_dir("PDSS")
    (sdir / "materials" / "lec.pdf").write_text("indexed copy")
    conn = store.connect(sdir / "store.db")
    with conn:
        conn.execute(
            "INSERT INTO materials (filename, sha256, status) VALUES ('lec.pdf', ?, 'indexed')",
            ("a" * 64,),
        )
        conn.execute(
            "INSERT INTO materials (filename, sha256, status, error) "
            "VALUES ('lec.pdf', ?, 'extraction_failed', 'no readable text — OCR found nothing to extract')",
            ("b" * 64,),
        )
    conn.close()

    result = runner.invoke(app, ["remove", "PDSS", "b" * 8, "--yes"])
    assert result.exit_code == 0, result.output
    assert (sdir / "materials" / "lec.pdf").exists()  # indexed material's file survives
    conn = store.connect(sdir / "store.db")
    rows = conn.execute("SELECT status FROM materials").fetchall()
    assert [r["status"] for r in rows] == ["indexed"]
    conn.close()


def _seed_two(sha_a="a" * 64, sha_b="b" * 64):
    """Two indexed materials a.pdf and b.pdf in PDSS, each with its materials/ copy."""
    from groundly.core import store

    init_subject("PDSS")
    sdir = subject_dir("PDSS")
    conn = store.connect(sdir / "store.db")
    with conn:
        for name, sha in (("a.pdf", sha_a), ("b.pdf", sha_b)):
            (sdir / "materials" / name).write_text("x")
            conn.execute(
                "INSERT INTO materials (filename, sha256, status) VALUES (?, ?, 'indexed')",
                (name, sha),
            )
    conn.close()
    return sdir


def _material_names(sdir):
    from groundly.core import store

    conn = store.connect(sdir / "store.db")
    names = [r[0] for r in conn.execute("SELECT filename FROM materials ORDER BY filename")]
    conn.close()
    return names


def test_remove_requires_a_material():
    init_subject("PDSS")
    assert runner.invoke(app, ["remove", "PDSS"]).exit_code == 2


def test_remove_many_with_one_unknown_deletes_nothing():
    sdir = _seed_two()
    result = runner.invoke(app, ["remove", "PDSS", "a.pdf", "nope.pdf", "-y"])
    assert result.exit_code == 1
    assert "nope.pdf" in result.output
    assert _material_names(sdir) == ["a.pdf", "b.pdf"]
    assert (sdir / "materials" / "a.pdf").exists()


def test_remove_many_prompts_once_and_removes_all():
    sdir = _seed_two()
    result = runner.invoke(app, ["remove", "PDSS", "a.pdf", "b.pdf"], input="y\n")
    assert result.exit_code == 0, result.output
    assert result.output.count("Remove 2 material(s)") == 1
    assert _material_names(sdir) == []
    assert not (sdir / "materials" / "a.pdf").exists()


def test_remove_same_material_twice_removes_it_once():
    sdir = _seed_two()
    result = runner.invoke(app, ["remove", "PDSS", "a.pdf", "a" * 8, "-y"])
    assert result.exit_code == 0, result.output
    assert _material_names(sdir) == ["b.pdf"]


def test_drop_deletes_subject_directory():
    init_subject("PDSS")
    sdir = subject_dir("PDSS")
    (sdir / "store.db").unlink()  # even a damaged subject must be droppable
    result = runner.invoke(app, ["drop", "PDSS", "--yes"])
    assert result.exit_code == 0, result.output
    assert not sdir.exists()


def test_drop_aborts_without_confirmation():
    init_subject("PDSS")
    result = runner.invoke(app, ["drop", "PDSS"], input="n\n")
    assert result.exit_code != 0
    assert subject_dir("PDSS").exists()


def test_drop_prompt_names_progress_not_notes():
    init_subject("PDSS")
    result = runner.invoke(app, ["drop", "PDSS"], input="n\n")
    assert "progress" in result.output
    assert "notes" not in result.output


@pytest.mark.parametrize(
    "args",
    [
        ["index", "PDSS"],  # paths required
        ["config", "set", "chat.model"],  # value required
        ["search", "PDSS"],  # query required
    ],
)
def test_bad_usage_is_usage_error(args):
    assert runner.invoke(app, args).exit_code == 2


# --- index ---------------------------------------------------------------------------
# `pipeline.index_paths` is stubbed: the heavy logic is covered in test_pipeline.py.


def _stub_index_paths(monkeypatch, f):
    def fake_index_paths(
        subject, paths, embedder=None, on_event=None, on_discovered=None, ocr_lang=None, **_
    ):
        return [FileResult(f, Status.INDEXED, chunks=1)]

    monkeypatch.setattr(pipeline, "index_paths", fake_index_paths)


def test_index_creates_subject_on_first_run(monkeypatch, tmp_path, home):
    f = tmp_path / "a.md"
    f.write_text("# A\n\ntext\n")
    _stub_index_paths(monkeypatch, f)
    result = runner.invoke(app, ["index", "PDSS", str(f)])
    assert result.exit_code == 0, result.output
    assert (subject_dir("PDSS") / "manifest.json").exists()


def test_index_mistyped_path_creates_nothing(tmp_path, home):
    real = tmp_path / "a.md"
    real.write_text("x")
    result = runner.invoke(app, ["index", "PDSS", str(real), str(tmp_path / "sldes")])
    assert result.exit_code == 1
    assert "sldes" in result.output
    assert not subject_dir("PDSS").exists()


def test_init_verb_is_gone():
    assert runner.invoke(app, ["init", "PDSS"]).exit_code == 2


def test_index_retry_failed_flag_reaches_pipeline(monkeypatch, tmp_path, home):
    f = tmp_path / "lec.txt"
    f.write_text("content")
    seen = {}

    def fake_index_paths(subject, paths, **kwargs):
        seen.update(kwargs)
        return [FileResult(f, Status.INDEXED, chunks=1)]

    monkeypatch.setattr(pipeline, "index_paths", fake_index_paths)
    result = runner.invoke(app, ["index", "PDSS", str(f), "--retry-failed"])
    assert result.exit_code == 0, result.output
    assert seen["retry_failed"] is True


# --- --debug (debug-logging design) ---------------------------------------------------


def test_index_debug_disables_indexing_progress_bar(monkeypatch, home, tmp_path):
    import rich.progress

    disable_kwargs = []
    original_init = rich.progress.Progress.__init__

    def spy_init(self, *args, **kwargs):
        disable_kwargs.append(kwargs.get("disable"))
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(rich.progress.Progress, "__init__", spy_init)

    init_subject("PDSS")
    f = tmp_path / "lec.txt"
    f.write_text("content")
    _stub_index_paths(monkeypatch, f)

    result = runner.invoke(app, ["index", "PDSS", str(f), "--debug"])
    assert result.exit_code == 0, result.output
    assert disable_kwargs == [True]


def test_index_debug_invalid_log_level_fails_cleanly(monkeypatch, home, tmp_path):
    monkeypatch.setenv("GROUNDLY_LOG_LEVEL", "BOGUS")
    init_subject("PDSS")
    f = tmp_path / "lec.txt"
    f.write_text("content")
    _stub_index_paths(monkeypatch, f)

    result = runner.invoke(app, ["index", "PDSS", str(f)])
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "BOGUS" in result.output


def test_index_rejects_the_retired_graph_flag(tmp_path):
    f = tmp_path / "lec.txt"
    f.write_text("content")
    result = runner.invoke(app, ["index", "PDSS", str(f), "--graph"])
    assert result.exit_code == 2
