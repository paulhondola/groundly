"""Filesystem layout: ~/.groundly/ (GROUNDLY_HOME overrides), one dir per subject.

Discovery scans */manifest.json — no registry database (docs/architecture/data-model.md).
"""

import os
import re
from pathlib import Path

_SUBJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def groundly_home() -> Path:
    override = os.environ.get("GROUNDLY_HOME")
    return Path(override) if override else Path.home() / ".groundly"


def remote_db_path() -> Path:
    return groundly_home() / "remote.db"


def activity_db_path() -> Path:
    return groundly_home() / "activity.db"


def owner_only(path: Path) -> None:
    """Tighten a private state file to 0600 when group or others can reach it."""
    if os.stat(path).st_mode & 0o077:
        os.chmod(path, 0o600)


def validate_subject_name(name: str) -> None:
    """Subject names become path components and MCP identifiers."""
    if not _SUBJECT_RE.fullmatch(name):  # fullmatch: `$` alone accepts a trailing newline
        raise ValueError(
            f"invalid subject name {name!r} — use letters, digits, '-' or '_' "
            "(it becomes a directory name)"
        )


def subject_dir(name: str) -> Path:
    validate_subject_name(name)
    return groundly_home() / name


def discover_subjects() -> list[str]:
    return sorted(p.parent.name for p in groundly_home().glob("*/manifest.json"))
