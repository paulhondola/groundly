"""CLI: grammar pinned by the P1 surface design; init/list/remove exercised for real
against a temp GROUNDLY_HOME. Heavy index logic is covered in test_pipeline.py; the
CLI index test stubs the pipeline entry point."""

import pytest
from typer.testing import CliRunner

from groundly.cli import app
from groundly.core.paths import subject_dir
from groundly.ingestion import pipeline
from groundly.ingestion.results import FileResult, Status

runner = CliRunner()


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


def test_no_args_shows_help():
    assert "Usage" in runner.invoke(app, []).output


def test_init_creates_layout_and_is_idempotent(home):
    result = runner.invoke(app, ["init", "PDSS"])
    assert result.exit_code == 0, result.output
    sdir = home / "PDSS"
    for expected in ["manifest.json", "materials", "store.db", "progress.db"]:
        assert (sdir / expected).exists(), expected
    assert (home / "config.toml").exists()

    result = runner.invoke(app, ["init", "PDSS"])
    assert result.exit_code == 0
    assert "already initialized" in result.output


def test_init_rejects_bad_name():
    result = runner.invoke(app, ["init", "../evil"])
    assert result.exit_code == 1
    assert "invalid subject name" in result.output


def test_list_all_subjects():
    runner.invoke(app, ["init", "PDSS"])
    runner.invoke(app, ["init", "ML"])
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "PDSS" in result.output and "ML" in result.output


def test_list_unknown_subject_names_the_fix():
    result = runner.invoke(app, ["list", "NOPE"])
    assert result.exit_code == 1
    assert "groundly init NOPE" in result.output


def test_index_reports_results(monkeypatch, tmp_path):
    runner.invoke(app, ["init", "PDSS"])
    f = tmp_path / "lec.txt"
    f.write_text("content")

    def fake_index_paths(subject, paths, embedder=None, on_event=None, ocr_lang=None):
        return [FileResult(f, Status.INDEXED, chunks=3)]

    monkeypatch.setattr(pipeline, "index_paths", fake_index_paths)
    result = runner.invoke(app, ["index", "PDSS", str(f)])
    assert result.exit_code == 0, result.output
    assert "1 indexed" in result.output and "(3 chunks)" in result.output


def test_index_ocr_lang_set_reuse_mismatch(monkeypatch, tmp_path):
    """--ocr-lang: first use persists into the manifest; same value reuses; a
    different value is refused (re-index migration, decision 15)."""
    from groundly.core.manifest import Manifest

    runner.invoke(app, ["init", "PDSS"])
    f = tmp_path / "lec.txt"
    f.write_text("content")
    seen = []

    def fake_index_paths(subject, paths, embedder=None, on_event=None, ocr_lang=None):
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


def test_index_uninitialized_subject_fails_with_fix(tmp_path):
    f = tmp_path / "lec.txt"
    f.write_text("content")
    result = runner.invoke(app, ["index", "NOPE", str(f)])
    assert result.exit_code == 1
    assert "groundly init NOPE" in result.output


@pytest.mark.parametrize("args", [["list", "../evil"], ["remove", "../evil", "x.pdf", "-y"]])
def test_bad_subject_name_fails_cleanly_not_traceback(args):
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "invalid subject name" in result.output


def test_remove_unknown_material():
    runner.invoke(app, ["init", "PDSS"])
    result = runner.invoke(app, ["remove", "PDSS", "ghost.pdf", "-y"])
    assert result.exit_code == 1
    assert "no material" in result.output


def test_remove_deletes_rows_and_file(home):
    from groundly.core import store

    runner.invoke(app, ["init", "PDSS"])
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
    runner.invoke(app, ["init", "PDSS"])
    sdir = subject_dir("PDSS")
    (sdir / "store.db").unlink()
    result = runner.invoke(app, ["list", "PDSS"])
    assert result.exit_code == 1
    assert "store.db is missing" in result.output
    assert not (sdir / "store.db").exists()  # regression: connect() created an empty db


def test_list_empty_store_db_names_cause_not_traceback():
    runner.invoke(app, ["init", "PDSS"])
    sdir = subject_dir("PDSS")
    (sdir / "store.db").unlink()
    (sdir / "store.db").touch()  # exists but schema-less (e.g. interrupted init)
    result = runner.invoke(app, ["list", "PDSS"])
    assert result.exit_code == 1
    assert "corrupt or incomplete" in result.output


def test_list_all_skips_corrupt_manifest_with_warning():
    runner.invoke(app, ["init", "PDSS"])
    runner.invoke(app, ["init", "ML"])
    (subject_dir("PDSS") / "manifest.json").write_text("{ truncated")
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0  # one damaged subject must not take down the listing
    assert "ML" in result.output
    assert "PDSS" in result.output and "corrupt" in result.output


def test_remove_failed_row_keeps_indexed_siblings_file(home):
    """A failed row records the original filename with no collision suffix — removing
    it must not delete a same-named indexed material's stored file (citation target)."""
    from groundly.core import store

    runner.invoke(app, ["init", "PDSS"])
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


def test_remove_whole_subject_deletes_directory():
    runner.invoke(app, ["init", "PDSS"])
    sdir = subject_dir("PDSS")
    (sdir / "store.db").unlink()  # even a damaged subject must be removable
    result = runner.invoke(app, ["remove", "PDSS", "--yes"])
    assert result.exit_code == 0, result.output
    assert not sdir.exists()


def test_remove_whole_subject_aborts_without_confirmation():
    runner.invoke(app, ["init", "PDSS"])
    result = runner.invoke(app, ["remove", "PDSS"], input="n\n")
    assert result.exit_code != 0
    assert subject_dir("PDSS").exists()


@pytest.mark.parametrize(
    "args",
    [
        ["init"],  # subject required
        ["index", "PDSS"],  # paths required
        ["config", "set", "chat.model"],  # value required
        ["ask", "PDSS", "q"],  # P3 verb must NOT exist yet
    ],
)
def test_bad_usage_is_usage_error(args):
    assert runner.invoke(app, args).exit_code == 2
