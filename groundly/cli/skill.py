"""`groundly skill`: install the three packaged skills (map, questions, grade) into
~/.agents/skills/ and any present host (~/.claude, ~/.codex, ~/.gemini/config), and retire
the old combined one. Each installed copy is compared byte for byte with the packaged one,
so an edited or older copy is never silently overwritten or silently trusted, and no link
planted at a skill file is ever written through or removed."""

from importlib.resources import files
from pathlib import Path
from typing import Annotated

import typer

from groundly.cli.app import _fail, console, skill_app

CONNECT_COMMAND = "claude mcp add --scope user groundly -- groundly mcp"
CODEX_CONNECT_COMMAND = "codex mcp add groundly -- groundly mcp"
ANTIGRAVITY_CONNECT_HINT = (
    'add "groundly": {"command": "groundly", "args": ["mcp"]} to mcpServers in '
    "~/.gemini/config/mcp_config.json"
)
NAMES = ("groundly-map", "groundly-questions", "groundly-grade")
HOSTS = (".claude", ".codex", ".gemini/config")


def _all_dirs() -> list[Path]:
    home = Path.home()
    return [home / ".agents/skills", *(home / h / "skills" for h in HOSTS)]


def _active_dirs() -> list[Path]:
    home = Path.home()
    return [
        home / ".agents/skills",
        *(home / h / "skills" for h in HOSTS if (home / h).exists() or (home / h).is_symlink()),
    ]


def _legacy() -> Path:
    """The single skill earlier Groundly versions installed; left in place it would
    trigger beside the three."""
    return Path.home() / ".claude/skills/groundly/SKILL.md"


def _packaged(name: str) -> bytes:
    return files("groundly").joinpath("skills", name, "SKILL.md").read_bytes()


def _refuse_symlinks() -> None:
    """Never write through or unlink a link planted at a skill file: it could point
    anywhere the user can write. Checked for every path before anything is written;
    `is_symlink` also catches a dangling link. A linked skill folder or host root is
    allowed, but not one whose destination is gone: nothing could be written there."""
    for skills_dir in _all_dirs():
        for p in (skills_dir.parent, skills_dir):
            if p.is_symlink() and not p.exists():
                _fail(
                    f"{p} links to {p.readlink()}, which does not exist — fix or remove "
                    "the link, then re-run"
                )
    for target in (*(d / n / "SKILL.md" for d in _all_dirs() for n in NAMES), _legacy()):
        if target.is_symlink():
            _fail(f"{target} is a symlink to {target.readlink()} — remove it yourself, then re-run")
        folder = target.parent
        if folder.is_symlink() and not folder.exists():
            _fail(
                f"{folder} links to {folder.readlink()}, which does not exist — fix or remove "
                "the link, then re-run"
            )


def _remove(target: Path) -> None:
    if target.exists():
        target.unlink()
    # keep a folder that still holds the user's files, is linked to by another skill folder,
    # or is itself a linked folder
    folder = target.parent
    if not folder.exists() or folder.is_symlink() or any(folder.iterdir()):
        return
    resolved = folder.resolve()
    linked_folders = [
        d / n for d in _all_dirs() for n in NAMES if (d / n).is_symlink() and (d / n).exists()
    ]
    if any(lf.resolve() == resolved for lf in linked_folders):
        return
    folder.rmdir()


@skill_app.callback(invoke_without_command=True)
def skill(ctx: typer.Context) -> None:
    """Show whether each Groundly skill is installed across active agent directories."""
    if ctx.invoked_subcommand is not None:
        return
    for skills_dir in _active_dirs():
        for name in NAMES:
            target = skills_dir / name / "SKILL.md"
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
    """Install the Groundly skills into ~/.agents/skills/ and detected agent directories,
    and retire the old combined one."""
    _refuse_symlinks()
    skipped: list[Path] = []
    declined: set[Path] = set()
    for skills_dir in _active_dirs():
        for name in NAMES:
            target, packaged = skills_dir / name / "SKILL.md", _packaged(name)
            if target.exists():
                if target.read_bytes() == packaged:
                    console.print(f"{name}: already installed  ({target})", soft_wrap=True)
                    continue
                if target.resolve() in declined:
                    continue
                if not yes and not typer.confirm(f"replace the different copy at {target}?"):
                    skipped.append(target)
                    declined.add(target.resolve())
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
    console.print(f"Connect Codex to Groundly: {CODEX_CONNECT_COMMAND}", soft_wrap=True)
    console.print(f"Connect Antigravity to Groundly: {ANTIGRAVITY_CONNECT_HINT}", soft_wrap=True)
    console.print("Then, in your agent, say: map my course")


@skill_app.command()
def uninstall(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Remove the Groundly skills (and the old combined one) from all agent directories."""
    _refuse_symlinks()
    candidates = (*(d / n / "SKILL.md" for d in _all_dirs() for n in NAMES), _legacy())
    present: list[Path] = []
    seen: set[Path] = set()
    for target in candidates:
        if target.exists() and target.resolve() not in seen:
            present.append(target)
            seen.add(target.resolve())
    if not present:
        console.print("not installed — nothing to do")
        return
    if not yes:
        for target in present:
            console.print(f"  {target}", soft_wrap=True)
        typer.confirm(f"remove these {len(present)} Groundly skill files?", abort=True)
    for target in present:
        _remove(target)
        console.print(f"removed {target}", soft_wrap=True)
