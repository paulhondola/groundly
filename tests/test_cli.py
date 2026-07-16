"""CLI: grammar pinned by the P1 surface design; init/list/remove exercised for real
against a temp UNILEARN_HOME. Heavy index logic is covered in test_pipeline.py; the
CLI index test stubs the pipeline entry point."""

import pytest
from typer.testing import CliRunner

from unilearn.cli import app
from unilearn.core.paths import subject_dir
from unilearn.ingestion import pipeline

runner = CliRunner()


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("UNILEARN_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == "0.1.0"


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
    assert "unilearn init NOPE" in result.output


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
    assert "unilearn init NOPE" in result.output


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
    from unilearn.core import store

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
        ["remove", "PDSS"],  # material required
        ["config", "set", "chat.model"],  # value required
        ["ask", "PDSS", "q"],  # P3 verb must NOT exist yet
    ],
)
def test_bad_usage_is_usage_error(args):
    assert runner.invoke(app, args).exit_code == 2
