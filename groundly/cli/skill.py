"""`groundly skill`: install the three packaged Claude Code skills (map, questions, grade)
and retire the old combined one. Each installed copy is compared byte for byte with the
packaged one, so an edited or older copy is never silently overwritten or silently trusted,
and no link planted at a skill file is ever written through or removed."""

from importlib.resources import files
from pathlib import Path
from typing import Annotated

import typer

from groundly.cli.app import _fail, console, skill_app

CONNECT_COMMAND = "claude mcp add --scope user groundly -- groundly mcp"
NAMES = ("groundly-map", "groundly-questions", "groundly-grade")


def _target(name: str) -> Path:
    return Path.home() / ".claude/skills" / name / "SKILL.md"


def _legacy() -> Path:
    """The single skill earlier Groundly versions installed; left in place it would
    trigger beside the three."""
    return _target("groundly")


def _packaged(name: str) -> bytes:
    return files("groundly").joinpath("skills", name, "SKILL.md").read_bytes()


def _refuse_symlinks() -> None:
    """Never write through or unlink a link planted at a skill file: it could point
    anywhere the user can write. Checked for every path before anything is written;
    `is_symlink` also catches a dangling link."""
    for target in (*map(_target, NAMES), _legacy()):
        if target.is_symlink():
            _fail(f"{target} is a symlink to {target.readlink()} — remove it yourself, then re-run")


def _remove(target: Path) -> None:
    target.unlink()
    # keep a folder that still holds the user's files, and never remove a linked folder
    if not target.parent.is_symlink() and not any(target.parent.iterdir()):
        target.parent.rmdir()


@skill_app.callback(invoke_without_command=True)
def skill(ctx: typer.Context) -> None:
    """Show whether each Groundly skill is installed for Claude Code."""
    if ctx.invoked_subcommand is not None:
        return
    for name in NAMES:
        target = _target(name)
        if not target.exists():
            state = "not installed — run: groundly skill install"
        elif target.read_bytes() == _packaged(name):
            state = "installed"
        else:
            state = "out of date — run: groundly skill install"
        console.print(f"{name}: {state}  ({target})", soft_wrap=True)
    if _legacy().exists():
        console.print(
            f"old combined skill still installed — run: groundly skill install to replace it"
            f"  ({_legacy()})",
            soft_wrap=True,
        )


@skill_app.command()
def install(
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Skip the confirmation prompts.")
    ] = False,
) -> None:
    """Install the Groundly skills into ~/.claude/skills/ and retire the old combined one."""
    _refuse_symlinks()
    skipped: list[Path] = []
    for name in NAMES:
        target, packaged = _target(name), _packaged(name)
        if target.exists():
            if target.read_bytes() == packaged:
                console.print(f"{name}: already installed", soft_wrap=True)
                continue
            if not yes and not typer.confirm(f"replace the different copy at {target}?"):
                skipped.append(target)
                continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(packaged)
        console.print(f"installed {target}", soft_wrap=True)

    legacy = _legacy()
    if legacy.exists():
        if yes or typer.confirm(f"remove the old combined skill at {legacy}?"):
            _remove(legacy)
            console.print(f"removed the old combined skill {legacy}", soft_wrap=True)
        else:
            skipped.append(legacy)
            console.print(
                "the old combined skill will keep triggering beside the new ones until removed",
                soft_wrap=True,
            )

    for target in skipped:
        console.print(f"skipped {target}", soft_wrap=True)
    console.print(f"Connect Claude Code to Groundly: {CONNECT_COMMAND}", soft_wrap=True)
    console.print("Then, in Claude Code, say: map my course")


@skill_app.command()
def uninstall(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Remove the Groundly skills (and the old combined one) from ~/.claude/skills/."""
    _refuse_symlinks()
    present = [t for t in (*map(_target, NAMES), _legacy()) if t.exists()]
    if not present:
        console.print("not installed — nothing to do")
        return
    if not yes:
        typer.confirm(f"remove {len(present)} Groundly skill files?", abort=True)
    for target in present:
        _remove(target)
        console.print(f"removed {target}", soft_wrap=True)
