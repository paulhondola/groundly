"""CLI: grammar pinned by the P1 surface design; init/list/remove exercised for real
against a temp GROUNDLY_HOME. Heavy index logic is covered in test_pipeline.py; the
CLI index test stubs the pipeline entry point."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from groundly.cli import app
from groundly.core.paths import subject_dir
from groundly.ingestion import pipeline
from groundly.llm import embeddings

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

    def fake_index_paths(subject, paths, embedder=None, on_event=None):
        return [pipeline.FileResult(f, pipeline.INDEXED, chunks=3)]

    monkeypatch.setattr(pipeline, "index_paths", fake_index_paths)
    result = runner.invoke(app, ["index", "PDSS", str(f)])
    assert result.exit_code == 0, result.output
    assert "1 indexed" in result.output and "(3 chunks)" in result.output


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
            "VALUES ('lec.pdf', ?, 'extraction_failed', 'scanned PDF — not supported')",
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


@pytest.mark.parametrize("args", [["config"], ["config", "set", "chat.model", "x"]])
def test_config_still_stubbed(args):
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "not implemented yet" in result.output


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


def test_models_install_cache_hit_skips_download(monkeypatch):
    monkeypatch.setattr(embeddings, "cached_snapshot", lambda: Path("/fake/cached"))

    def must_not_download(force=False):
        raise AssertionError("must not download on cache hit")

    monkeypatch.setattr(embeddings, "ensure_downloaded", must_not_download)
    result = runner.invoke(app, ["models", "install"])
    assert result.exit_code == 0, result.output
    assert "already cached" in result.output


def test_models_install_cache_miss_downloads(monkeypatch):
    monkeypatch.setattr(embeddings, "cached_snapshot", lambda: None)
    calls = {}

    def fake_ensure(force=False):
        calls["force"] = force
        return Path("/fake/downloaded")

    monkeypatch.setattr(embeddings, "ensure_downloaded", fake_ensure)
    result = runner.invoke(app, ["models", "install"])
    assert result.exit_code == 0, result.output
    assert calls["force"] is False
    assert "ready" in result.output


def test_models_install_force_downloads_even_if_cached(monkeypatch):
    monkeypatch.setattr(embeddings, "cached_snapshot", lambda: Path("/fake/cached"))
    calls = {"n": 0}

    def fake_ensure(force=False):
        calls["n"] += 1
        calls["force"] = force
        return Path("/fake/cached")

    monkeypatch.setattr(embeddings, "ensure_downloaded", fake_ensure)
    result = runner.invoke(app, ["models", "install", "--force"])
    assert result.exit_code == 0, result.output
    assert calls == {"n": 1, "force": True}


def test_models_install_download_failure_names_cause_not_traceback(monkeypatch):
    monkeypatch.setattr(embeddings, "cached_snapshot", lambda: None)

    def fake_ensure(force=False):
        raise embeddings.ModelDownloadError("failed to download BAAI/bge-m3: connection reset")

    monkeypatch.setattr(embeddings, "ensure_downloaded", fake_ensure)
    result = runner.invoke(app, ["models", "install"])
    assert result.exit_code == 1
    assert "failed to download" in result.output


def test_models_uninstall_not_cached_is_a_noop(monkeypatch):
    monkeypatch.setattr(embeddings, "cached_snapshot", lambda: None)

    def must_not_remove():
        raise AssertionError("must not remove when nothing is cached")

    monkeypatch.setattr(embeddings, "remove_cached", must_not_remove)
    result = runner.invoke(app, ["models", "uninstall"])
    assert result.exit_code == 0, result.output
    assert "not cached" in result.output


def test_models_uninstall_removes_with_confirmation(monkeypatch):
    monkeypatch.setattr(embeddings, "cached_snapshot", lambda: Path("/fake/cached"))
    calls = {"n": 0}

    def fake_remove():
        calls["n"] += 1
        return True

    monkeypatch.setattr(embeddings, "remove_cached", fake_remove)
    result = runner.invoke(app, ["models", "uninstall", "--yes"])
    assert result.exit_code == 0, result.output
    assert calls["n"] == 1
    assert "removed" in result.output


def test_models_uninstall_aborts_without_confirmation(monkeypatch):
    monkeypatch.setattr(embeddings, "cached_snapshot", lambda: Path("/fake/cached"))

    def must_not_remove():
        raise AssertionError("must not remove without confirmation")

    monkeypatch.setattr(embeddings, "remove_cached", must_not_remove)
    result = runner.invoke(app, ["models", "uninstall"], input="n\n")
    assert result.exit_code != 0
