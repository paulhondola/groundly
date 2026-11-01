"""`groundly skill`: install the packaged Claude Code skill that drives the topic-tree
workflow. The installed copy is compared byte for byte with the packaged one, so an edited
or older copy is never silently overwritten or silently trusted."""

from importlib.resources import files
from pathlib import Path
from typing import Annotated

import typer

from groundly.cli.app import console, skill_app

CONNECT_COMMAND = "claude mcp add groundly -- groundly mcp"


def _target() -> Path:
    return Path.home() / ".claude/skills/groundly/SKILL.md"


def _packaged() -> bytes:
    return files("groundly").joinpath("skill/SKILL.md").read_bytes()


@skill_app.callback(invoke_without_command=True)
def skill(ctx: typer.Context) -> None:
    """Show whether the Groundly skill is installed for Claude Code."""
    if ctx.invoked_subcommand is not None:
        return
    target = _target()
    if not target.exists():
        console.print(f"not installed — run: groundly skill install  ({target})", soft_wrap=True)
    elif target.read_bytes() == _packaged():
        console.print(f"installed  ({target})", soft_wrap=True)
    else:
        console.print(f"out of date — run: groundly skill install  ({target})", soft_wrap=True)


@skill_app.command()
def install(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Install the Groundly skill into ~/.claude/skills/groundly/."""
    target = _target()
    packaged = _packaged()
    if target.exists():
        if target.read_bytes() == packaged:
            console.print("already installed — nothing to do")
            return
        if not yes:
            typer.confirm(f"replace the different copy at {target}?", abort=True)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(packaged)
    console.print(f"installed {target}", soft_wrap=True)
    console.print(f"Connect Claude Code to Groundly: {CONNECT_COMMAND}", soft_wrap=True)
    console.print("Then, in Claude Code, say: map my course")


@skill_app.command()
def uninstall(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Remove the Groundly skill from ~/.claude/skills/groundly/."""
    target = _target()
    if not target.exists():
        console.print("not installed — nothing to do")
        return
    if not yes:
        typer.confirm(f"remove {target}?", abort=True)

    target.unlink()
    if not any(target.parent.iterdir()):  # keep a folder that still holds the user's files
        target.parent.rmdir()
    console.print(f"removed {target}", soft_wrap=True)
