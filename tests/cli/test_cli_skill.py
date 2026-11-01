"""CLI: `groundly skill` status, install and uninstall."""

from importlib.resources import files

import pytest
from typer.testing import CliRunner

from groundly.cli import app

runner = CliRunner()

PACKAGED = files("groundly").joinpath("skill/SKILL.md").read_bytes()


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def target(home):
    return home / ".claude/skills/groundly/SKILL.md"


def test_status_reports_not_installed():
    result = runner.invoke(app, ["skill"])
    assert result.exit_code == 0, result.output
    assert "not installed" in result.output


def test_install_copies_packaged_skill(target):
    result = runner.invoke(app, ["skill", "install"])
    assert result.exit_code == 0, result.output
    assert target.read_bytes() == PACKAGED
    assert "claude mcp add groundly -- groundly mcp" in result.output
    assert "map my course" in result.output


def test_status_reports_installed_then_out_of_date(target):
    runner.invoke(app, ["skill", "install"])
    result = runner.invoke(app, ["skill"])
    assert "installed" in result.output
    assert "not installed" not in result.output
    assert "out of date" not in result.output

    target.write_text("edited")
    result = runner.invoke(app, ["skill"])
    assert "out of date" in result.output
    assert "not installed" not in result.output


def test_install_prompts_before_replacing_a_different_copy(target):
    runner.invoke(app, ["skill", "install"])
    target.write_text("edited")
    result = runner.invoke(app, ["skill", "install"], input="n\n")
    assert result.exit_code != 0
    assert target.read_text() == "edited"


def test_install_yes_replaces_without_prompt(target):
    runner.invoke(app, ["skill", "install"])
    target.write_text("edited")
    result = runner.invoke(app, ["skill", "install", "-y"])
    assert result.exit_code == 0, result.output
    assert target.read_bytes() == PACKAGED


def test_install_is_silent_when_identical(target):
    runner.invoke(app, ["skill", "install"])
    result = runner.invoke(app, ["skill", "install"])  # no input: a prompt would abort
    assert result.exit_code == 0, result.output
    assert target.read_bytes() == PACKAGED


def test_uninstall_removes_skill_and_empty_folder(target):
    runner.invoke(app, ["skill", "install"])
    result = runner.invoke(app, ["skill", "uninstall", "-y"])
    assert result.exit_code == 0, result.output
    assert not target.parent.exists()


def test_uninstall_keeps_other_files_in_the_folder(target):
    runner.invoke(app, ["skill", "install"])
    extra = target.parent / "notes.md"
    extra.write_text("mine")
    result = runner.invoke(app, ["skill", "uninstall", "-y"])
    assert result.exit_code == 0, result.output
    assert not target.exists()
    assert extra.read_text() == "mine"


def test_uninstall_when_not_installed_says_nothing_to_do():
    result = runner.invoke(app, ["skill", "uninstall"])
    assert result.exit_code == 0, result.output
    assert "nothing to do" in result.output
