"""CLI: `groundly skill` status, install and uninstall of the three packaged skills."""

from importlib.resources import files

import pytest
from typer.testing import CliRunner

from groundly.cli import app

runner = CliRunner()

NAMES = ("groundly-map", "groundly-questions", "groundly-grade")
PACKAGED = {n: files("groundly").joinpath("skills", n, "SKILL.md").read_bytes() for n in NAMES}
CONNECT = "claude mcp add --scope user groundly -- groundly mcp"


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def targets(home):
    return {n: home / ".agents/skills" / n / "SKILL.md" for n in NAMES}


@pytest.fixture
def legacy(home):
    return home / ".claude/skills/groundly/SKILL.md"


def _plant_legacy(legacy, extra=None):
    legacy.parent.mkdir(parents=True)
    legacy.write_text("old combined skill")
    if extra:
        (legacy.parent / extra).write_text("mine")


def _line(output: str, name: str) -> str:
    (line,) = [ln for ln in output.splitlines() if ln.startswith(f"{name}:")]
    return line


def _flat(result) -> str:
    return result.output.replace("\n", "")


def test_status_reports_each_skill_not_installed():
    result = runner.invoke(app, ["skill"])
    assert result.exit_code == 0, result.output
    for name in NAMES:
        assert "not installed" in _line(result.output, name)


def test_install_copies_all_three_and_says_how_to_connect(targets):
    result = runner.invoke(app, ["skill", "install"])
    assert result.exit_code == 0, result.output
    for name, target in targets.items():
        assert target.read_bytes() == PACKAGED[name]
    assert CONNECT in _flat(result) and "map my course" in result.output


def test_status_reports_installed_then_out_of_date_per_skill(targets):
    runner.invoke(app, ["skill", "install"])
    result = runner.invoke(app, ["skill"])
    assert "not installed" not in result.output and "out of date" not in result.output

    targets["groundly-grade"].write_text("edited")
    result = runner.invoke(app, ["skill"])
    assert "out of date" in _line(result.output, "groundly-grade")
    for name in ("groundly-map", "groundly-questions"):
        line = _line(result.output, name)
        assert "installed" in line and "not installed" not in line and "out of date" not in line


def test_a_no_skips_only_that_skill(targets, legacy):
    runner.invoke(app, ["skill", "install"])
    targets["groundly-grade"].write_text("edited")
    _plant_legacy(legacy)

    result = runner.invoke(app, ["skill", "install"], input="n\ny\n")

    assert result.exit_code == 0, result.output
    assert targets["groundly-grade"].read_text() == "edited"
    for name in ("groundly-map", "groundly-questions"):
        assert targets[name].read_bytes() == PACKAGED[name]
    assert not legacy.exists()
    assert "skipped" in _flat(result) and str(targets["groundly-grade"]) in _flat(result)


def test_install_yes_replaces_without_prompt(targets):
    runner.invoke(app, ["skill", "install"])
    for target in targets.values():
        target.write_text("edited")
    result = runner.invoke(app, ["skill", "install", "-y"])
    assert result.exit_code == 0, result.output
    for name, target in targets.items():
        assert target.read_bytes() == PACKAGED[name]


def test_install_is_silent_when_identical(targets):
    runner.invoke(app, ["skill", "install"])
    result = runner.invoke(app, ["skill", "install"])  # no input: a prompt would read EOF
    assert result.exit_code == 0, result.output
    assert "already installed" in result.output and "skipped" not in result.output


def test_install_retires_the_old_skill_after_a_prompt(legacy):
    _plant_legacy(legacy)
    result = runner.invoke(app, ["skill", "install"], input="y\n")
    assert result.exit_code == 0, result.output
    assert not legacy.parent.exists()
    status = runner.invoke(app, ["skill"]).output
    lines = [ln for ln in status.splitlines() if any(ln.startswith(f"{n}:") for n in NAMES)]
    assert len(lines) == 6 and all("installed" in ln for ln in lines)
    assert "not installed" not in status and "old combined skill" not in status


def test_declining_the_old_skills_removal_keeps_it_and_warns(legacy):
    _plant_legacy(legacy)
    result = runner.invoke(app, ["skill", "install"], input="n\n")
    assert result.exit_code == 0, result.output
    assert legacy.read_text() == "old combined skill"
    assert "will keep triggering" in _flat(result)


def test_retiring_keeps_the_users_other_files_in_the_old_folder(legacy):
    _plant_legacy(legacy, "notes.md")
    result = runner.invoke(app, ["skill", "install", "-y"])
    assert result.exit_code == 0, result.output
    assert not legacy.exists()
    assert (legacy.parent / "notes.md").read_text() == "mine"


def test_status_flags_a_leftover_old_skill(legacy):
    _plant_legacy(legacy)
    result = runner.invoke(app, ["skill"])
    assert result.exit_code == 0, result.output
    assert "old combined skill" in result.output


def test_uninstall_removes_all_three_and_empty_folders(targets):
    runner.invoke(app, ["skill", "install"])
    result = runner.invoke(app, ["skill", "uninstall", "-y"])
    assert result.exit_code == 0, result.output
    assert not any(t.parent.exists() for t in targets.values())


def test_uninstall_removes_an_old_skill_alone(legacy):
    _plant_legacy(legacy)
    result = runner.invoke(app, ["skill", "uninstall", "-y"])
    assert result.exit_code == 0, result.output
    assert not legacy.parent.exists()


def test_uninstall_prompts_once_and_a_no_removes_nothing(targets):
    runner.invoke(app, ["skill", "install"])
    result = runner.invoke(app, ["skill", "uninstall"], input="n\n")
    assert result.exit_code != 0
    assert all(t.exists() for t in targets.values())
    assert all(str(t) in _flat(result) for t in targets.values())  # names what it would remove


@pytest.mark.parametrize("command", [["install"], ["install", "-y"], ["uninstall", "-y"]])
def test_a_skill_folder_linked_to_nowhere_is_refused_by_name(home, targets, command):
    folder = targets["groundly-grade"].parent
    folder.parent.mkdir(parents=True)
    folder.symlink_to(home / "moved-away")

    result = runner.invoke(app, ["skill", *command])

    assert result.exit_code != 0 and not isinstance(result.exception, OSError)  # no traceback
    assert "does not exist" in _flat(result) and str(home / "moved-away") in _flat(result)
    assert not any(t.exists() for t in targets.values())


def test_uninstall_keeps_other_files_in_the_folder(targets):
    runner.invoke(app, ["skill", "install"])
    extra = targets["groundly-map"].parent / "notes.md"
    extra.write_text("mine")
    result = runner.invoke(app, ["skill", "uninstall", "-y"])
    assert result.exit_code == 0, result.output
    assert not targets["groundly-map"].exists()
    assert extra.read_text() == "mine"


def test_uninstall_when_not_installed_says_nothing_to_do():
    result = runner.invoke(app, ["skill", "uninstall"])
    assert result.exit_code == 0, result.output
    assert "nothing to do" in result.output


@pytest.mark.parametrize("dangling", [True, False])
@pytest.mark.parametrize("command", [["install"], ["install", "--yes"], ["uninstall", "--yes"]])
@pytest.mark.parametrize("which", [*NAMES, "legacy"])
def test_a_symlinked_skill_file_is_refused_and_never_followed(
    home, targets, legacy, which, command, dangling
):
    link = legacy if which == "legacy" else targets[which]
    destination = home / "elsewhere.txt"
    if not dangling:
        destination.write_text("mine")
    link.parent.mkdir(parents=True)
    link.symlink_to(destination)

    result = runner.invoke(app, ["skill", *command])

    assert result.exit_code != 0
    assert "symlink" in _flat(result) and str(destination) in _flat(result)
    assert link.is_symlink()
    assert destination.exists() is not dangling
    if not dangling:
        assert destination.read_text() == "mine"
    assert not any(t.exists() for n, t in targets.items() if t != link)


def test_a_symlinked_parent_directory_is_still_allowed(home, targets):
    target = targets["groundly-map"]
    real = home / "dotfiles/groundly-map"
    real.mkdir(parents=True)
    target.parent.parent.mkdir(parents=True)
    target.parent.symlink_to(real)

    result = runner.invoke(app, ["skill", "install"])

    assert result.exit_code == 0, result.output
    assert (real / "SKILL.md").read_bytes() == PACKAGED["groundly-map"]


@pytest.mark.parametrize("command", [["install", "-y"], ["uninstall", "-y"]])
def test_an_old_skill_in_a_symlinked_folder_is_removed_without_touching_the_link(
    home, legacy, command
):
    real = home / "dotfiles/groundly"
    real.mkdir(parents=True)
    legacy.parent.parent.mkdir(parents=True)
    legacy.parent.symlink_to(real)
    legacy.write_text("old combined skill")

    result = runner.invoke(app, ["skill", *command])

    assert result.exit_code == 0, result.output
    assert not (real / "SKILL.md").exists()
    assert legacy.parent.is_symlink()


@pytest.mark.parametrize("host", [".claude", ".codex", ".gemini/config"])
def test_detected_host_roots_are_installed_and_reported_beside_agents(home, host):
    (home / host).mkdir(parents=True)
    result = runner.invoke(app, ["skill", "install"])
    assert result.exit_code == 0, result.output
    for name in NAMES:
        assert (home / ".agents/skills" / name / "SKILL.md").read_bytes() == PACKAGED[name]
        assert (home / host / "skills" / name / "SKILL.md").read_bytes() == PACKAGED[name]
    for absent in {".claude", ".codex", ".gemini/config"} - {host}:
        assert not (home / absent).exists()
    status = runner.invoke(app, ["skill"]).output
    assert str(home / host / "skills/groundly-map/SKILL.md") in status


def test_a_symlinked_host_root_is_detected_and_installed(home):
    real = home / "dotfiles/claude/.claude"
    real.mkdir(parents=True)
    (home / ".claude").symlink_to(real)
    result = runner.invoke(app, ["skill", "install"])
    assert result.exit_code == 0, result.output
    for name in NAMES:
        assert (real / "skills" / name / "SKILL.md").read_bytes() == PACKAGED[name]


def test_a_dangling_host_root_symlink_is_refused_by_name(home):
    (home / ".codex").symlink_to(home / "gone")
    result = runner.invoke(app, ["skill", "install"])
    assert result.exit_code != 0
    assert "does not exist" in _flat(result) and str(home / "gone") in _flat(result)
    assert not (home / ".agents/skills").exists()


def test_a_host_skill_folder_symlinked_to_agents_survives_install_and_uninstall(home):
    agents_map = home / ".agents/skills/groundly-map"
    agents_map.mkdir(parents=True)
    claude_skills = home / ".claude/skills"
    claude_skills.mkdir(parents=True)
    (claude_skills / "groundly-map").symlink_to(agents_map)

    result = runner.invoke(app, ["skill", "install"])
    assert result.exit_code == 0, result.output
    assert (agents_map / "SKILL.md").read_bytes() == PACKAGED["groundly-map"]

    result = runner.invoke(app, ["skill", "uninstall", "-y"])
    assert result.exit_code == 0, result.output
    assert not (agents_map / "SKILL.md").exists()
    assert (claude_skills / "groundly-map").exists()  # link not left dangling

    result = runner.invoke(app, ["skill", "install"])
    assert result.exit_code == 0, result.output
    assert (agents_map / "SKILL.md").read_bytes() == PACKAGED["groundly-map"]


def test_a_no_in_one_host_skips_only_that_copy(home):
    (home / ".claude").mkdir()
    runner.invoke(app, ["skill", "install"])
    claude_grade = home / ".claude/skills/groundly-grade/SKILL.md"
    claude_grade.write_text("edited")

    result = runner.invoke(app, ["skill", "install"], input="n\n")
    assert result.exit_code == 0, result.output
    assert claude_grade.read_text() == "edited"
    assert (home / ".agents/skills/groundly-grade/SKILL.md").read_bytes() == PACKAGED[
        "groundly-grade"
    ]
    assert "skipped" in _flat(result) and str(claude_grade) in _flat(result)


def test_uninstall_removes_across_all_roots(home):
    for host in (".claude", ".codex", ".gemini/config"):
        (home / host).mkdir(parents=True)
    runner.invoke(app, ["skill", "install"])
    result = runner.invoke(app, ["skill", "uninstall", "-y"])
    assert result.exit_code == 0, result.output
    for root in (".agents", ".claude", ".codex", ".gemini/config"):
        assert not any((home / root / "skills" / n).exists() for n in NAMES)
