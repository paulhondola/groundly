"""CLI: grammar pinned by the P1 surface design; init/list/remove exercised for real
against a temp GROUNDLY_HOME. Heavy index logic is covered in test_pipeline.py; the
CLI index test stubs the pipeline entry point."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from groundly.cli import app
from groundly.core.paths import subject_dir
from groundly.core.store import SubjectStore
from groundly.core.subject import Subject
from groundly.ingestion import pipeline
from groundly.ingestion.results import FileResult, Status
from tests.conftest import init_subject

runner = CliRunner()


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


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


# --- title ----------------------------------------------------------------------


def test_title_sets_a_label_and_leaves_the_subject_name_alone():
    init_subject("apd")
    result = runner.invoke(app, ["title", "apd", "Algoritmi Paraleli si Distribuiti"])
    assert result.exit_code == 0, result.output
    assert "Algoritmi Paraleli si Distribuiti" in result.output
    manifest = Subject("apd").load_manifest()
    assert manifest.title == "Algoritmi Paraleli si Distribuiti"
    assert manifest.subject == "apd"


def test_title_accepts_diacritics_and_strips_outer_space():
    init_subject("apd")
    assert runner.invoke(app, ["title", "apd", "  Paraleli și Distribuiți "]).exit_code == 0
    assert Subject("apd").load_manifest().title == "Paraleli și Distribuiți"


def test_title_empty_string_clears_it():
    init_subject("apd")
    runner.invoke(app, ["title", "apd", "Algoritmi"])
    assert runner.invoke(app, ["title", "apd", ""]).exit_code == 0
    assert Subject("apd").load_manifest().title == ""


@pytest.mark.parametrize(
    ("text", "cause"),
    [
        ("a" * 121, "120"),
        ("two\nlines", "invisible"),
        ("rtl\N{RIGHT-TO-LEFT OVERRIDE}override", "invisible"),
        ("selector\N{VARIATION SELECTOR-16}", "invisible"),
        ("tag\U000e0100", "invisible"),
        ("\N{HANGUL FILLER}", "invisible"),
        ("\N{BRAILLE PATTERN BLANK}", "invisible"),
    ],
)
def test_title_refuses_bad_text_naming_the_cause_and_stores_nothing(text, cause):
    init_subject("apd")
    result = runner.invoke(app, ["title", "apd", text])
    assert result.exit_code == 1
    assert cause in result.output
    assert Subject("apd").load_manifest().title == ""


def test_title_shows_exactly_what_was_set_without_rich_rewriting_it():
    init_subject("apd")
    result = runner.invoke(app, ["title", "apd", ":warning: Fine\\"])
    assert ":warning: Fine\\" in result.output
    assert ":warning: Fine\\" in runner.invoke(app, ["list"]).output


def test_title_unknown_subject_names_the_fix():
    result = runner.invoke(app, ["title", "NOPE", "Algoritmi"])
    assert result.exit_code == 1
    assert "not initialized" in result.output


def test_title_corrupt_manifest_names_the_cause_not_a_traceback():
    init_subject("apd")
    Subject("apd").manifest_path.write_text("{not json")
    result = runner.invoke(app, ["title", "apd", "Algoritmi"])
    assert result.exit_code == 1
    assert "manifest.json" in result.output


def test_list_all_shows_title_and_treats_it_as_text_not_markup():
    init_subject("apd")
    init_subject("ml")
    runner.invoke(app, ["title", "apd", "[/red] Parallel"])
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0, result.output
    assert "[/red] Parallel" in result.output
    assert "—" in result.output  # ml has no title


# --- hostile values from a bundle or a damaged home ----------------------------------

HOSTILE = "\x1b]52;c;cHduZWQ=\x07[/nosuch]\x9b31m\u202e"


def _raw_store(subject, *statements):
    """Raw SQL as a hostile bundle author has it: constraints off."""
    import sqlite3

    conn = sqlite3.connect(subject_dir(subject) / "store.db")
    try:
        conn.execute("PRAGMA ignore_check_constraints = ON")
        with conn:
            for statement, *params in statements:
                conn.execute(statement, *params)
    finally:
        conn.close()


def _assert_no_raw_control(output):
    for char in ("\x1b", "\x07", "\x9b", "\u202e"):
        assert char not in output


def _set_raw_manifest_lang(subject, lang):
    import json

    path = subject_dir(subject) / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["ocr"]["lang"] = [lang]
    path.write_text(json.dumps(manifest))


@pytest.mark.parametrize("lang", ["\x1b]52;c;cHduZWQ=\x07", "[/nosuch]"])
def test_list_all_skips_a_hostile_ocr_lang(lang):
    init_subject("EVIL")
    init_subject("ML")
    _set_raw_manifest_lang("EVIL", lang)

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0, result.output
    assert "ML" in result.output and "EVIL" in result.output and "corrupt" in result.output
    _assert_no_raw_control(result.output)


def test_index_refuses_a_bad_ocr_lang_before_storing_it(tmp_path):
    f = tmp_path / "lec.txt"
    f.write_text("content")

    result = runner.invoke(app, ["index", "PDSS", str(f), "--ocr-lang", "[/nosuch]"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "OCR language" in " ".join(result.output.split())
    assert not Subject("PDSS").exists()


def test_list_subject_prints_hostile_store_fields_inert():
    init_subject("PDSS")
    _raw_store(
        "PDSS",
        (
            "INSERT INTO materials (filename, sha256, status, pages, error) VALUES (?, ?, ?, ?, ?)",
            (f"a{HOSTILE}.pdf", HOSTILE, HOSTILE, HOSTILE, HOSTILE),
        ),
    )

    result = runner.invoke(app, ["list", "PDSS"])

    assert result.exit_code == 0, result.output
    assert "[/nosuch]" in result.output  # shown literally, not parsed
    _assert_no_raw_control(result.output)


def test_list_subject_prints_an_emoji_code_literally():
    init_subject("PDSS")
    SubjectStore(subject_dir("PDSS") / "store.db").add_extraction_failed(
        ":warning:.pdf", "a" * 64, "no text"
    )

    assert ":warning:.pdf" in runner.invoke(app, ["list", "PDSS"]).output


def test_list_subject_survives_non_text_store_fields():
    init_subject("PDSS")
    _raw_store(
        "PDSS",
        (
            (
                "INSERT INTO materials (filename, sha256, status, pages, error) "
                "VALUES (x'00ff', x'00ff', x'00ff', x'00ff', x'00ff')"
            ),
        ),
    )

    result = runner.invoke(app, ["list", "PDSS"])

    assert result.exit_code == 0, result.output


def test_list_all_shows_a_dash_for_pages_that_are_not_numbers():
    init_subject("EVIL")
    init_subject("ML")
    _raw_store(
        "EVIL",
        (
            (
                "INSERT INTO materials (filename, sha256, status, pages) "
                "VALUES ('a.pdf', ?, 'indexed', 'many')"
            ),
            ("a" * 64,),
        ),
    )

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0, result.output
    assert "ML" in result.output
    assert "—" in next(line for line in result.output.splitlines() if "EVIL" in line)


def _manifest_not_utf8(path):
    path.write_bytes(b"\xff\xfe not utf-8")


def _manifest_is_a_directory(path):
    path.unlink()
    path.mkdir()


def _manifest_dangles(path):
    path.unlink()
    path.symlink_to(path.parent / "gone.json")


@pytest.mark.parametrize(
    "damage", [_manifest_not_utf8, _manifest_is_a_directory, _manifest_dangles]
)
def test_list_all_warns_about_an_unreadable_manifest(damage):
    init_subject("PDSS")
    init_subject("ML")
    damage(subject_dir("PDSS") / "manifest.json")

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0, result.output
    assert "ML" in result.output


@pytest.mark.parametrize("name", ["bad name", "evil\x1b[31m", "[nosuch]", ".hidden"])
def test_list_all_warns_about_a_badly_named_directory(home, name):
    init_subject("ML")
    (home / name).mkdir()
    (home / name / "manifest.json").write_text("{}")

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0, result.output
    assert "ML" in result.output
    _assert_no_raw_control(result.output)


def test_remove_never_deletes_outside_materials(home):
    victim = home / "victim.txt"
    victim.write_text("keep me")
    init_subject("PDSS")
    _raw_store(
        "PDSS",
        (
            (
                "INSERT INTO materials (filename, sha256, status, pages) "
                "VALUES ('../../victim.txt', ?, 'indexed', 1)"
            ),
            ("a" * 64,),
        ),
    )

    result = runner.invoke(app, ["remove", "PDSS", "aaaaaaaa", "-y"])

    assert result.exit_code == 0, result.output
    assert victim.read_text() == "keep me"


def test_remove_prompt_prints_a_hostile_filename_inert():
    init_subject("PDSS")
    _raw_store(
        "PDSS",
        (
            (
                "INSERT INTO materials (filename, sha256, status, pages) "
                "VALUES (?, ?, 'extraction_failed', 1)"
            ),
            (f"a{HOSTILE}.pdf", "a" * 64),
        ),
    )

    result = runner.invoke(app, ["remove", "PDSS", "aaaaaaaa"], input="n\n")

    assert "Remove 1 material" in result.output
    _assert_no_raw_control(result.output)


def test_index_progress_prints_a_hostile_filename_inert(monkeypatch, tmp_path):
    f = tmp_path / "lec.txt"
    f.write_text("content")
    init_subject("PDSS")

    def fake_index_paths(subject, paths, on_event=None, on_discovered=None, **_):
        on_discovered(1)
        on_event(Path("[bold]a\x1b]52;c;cHduZWQ=:warning:.txt"), "extracting")
        return [FileResult(f, Status.INDEXED, chunks=1)]

    from groundly.cli.app import console

    monkeypatch.setattr(pipeline, "index_paths", fake_index_paths)
    monkeypatch.setattr(console, "_force_terminal", True)  # the bar renders only on a terminal

    result = runner.invoke(app, ["index", "PDSS", str(f)])

    assert result.exit_code == 0, result.output
    assert "[bold]a" in result.output
    assert "\x1b]52;" not in result.output
    assert ":warning:" in result.output


def _groundly_on_ascii_stdout(home, *args):
    """A real process: Python's stdout encoding is fixed before any of this code runs."""
    return subprocess.run(
        [sys.executable, "-c", "from groundly.cli import app; app()", *args],
        check=False,
        capture_output=True,
        env={**os.environ, "GROUNDLY_HOME": str(home), "PYTHONIOENCODING": "ascii"},
    )


def test_ascii_stdout_replaces_non_ascii_instead_of_crashing(home):
    init_subject("PDSS")
    subject = Subject("PDSS")
    manifest = subject.load_manifest()
    manifest.title = "Programare în C — curs"
    subject.save_manifest(manifest)
    SubjectStore(subject.store_db_path).add_extraction_failed("curs_în_C.pdf", "a" * 64, "eșec")

    listing = _groundly_on_ascii_stdout(home, "list")
    materials = _groundly_on_ascii_stdout(home, "list", "PDSS")
    usage = _groundly_on_ascii_stdout(home, "--help")

    for result in (listing, materials, usage):
        assert result.returncode == 0, result.stderr.decode()
    assert b"Programare ?n C" in listing.stdout
    assert b"curs_?n_C.pdf" in materials.stdout
    # replacing is a property of the stream: nothing stored changed
    assert subject.load_manifest().title == "Programare în C — curs"
    assert SubjectStore(subject.store_db_path).list_materials()[0]["filename"] == "curs_în_C.pdf"
