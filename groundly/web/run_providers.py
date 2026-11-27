"""Discover CLIs on the serving machine and validate their launch options."""

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from groundly.web.schemas import ModelInfo, ProviderInfo, RunSelection

_REQUIRED_FLAGS = {
    "--print",
    "--mcp-config",
    "--strict-mcp-config",
    "--setting-sources",
    "--tools",
    "--allowedTools",
    "--permission-mode",
    "--output-format",
    "--verbose",
    "--no-session-persistence",
    "--include-partial-messages",
}
_TESTED_CLAUDE_VERSION = (2, 1, 318)
_UNAVAILABLE = {
    "no_host": "Claude Code (claude) is not on PATH",
    "unsupported_platform": "page runs need macOS or Linux",
    "incompatible_host": "this Claude Code executable does not support isolated page runs",
}


class RunError(Exception):
    """A run that cannot start, with a reason and HTTP status for the page route."""

    def __init__(self, reason: str, detail: str, status: int) -> None:
        super().__init__(detail)
        self.reason, self.detail, self.status = reason, detail, status


@dataclass(frozen=True)
class ResolvedProvider:
    executable: str
    selection: RunSelection


def availability() -> tuple[str | None, str | None]:
    if sys.platform not in {"darwin", "linux"}:
        return None, "unsupported_platform"
    path = shutil.which("claude")
    return (path, None) if path else (None, "no_host")


@lru_cache(maxsize=16)
def _claude_info(path: str, _identity: tuple[int, int, int, int]) -> ProviderInfo:
    def probe(flag: str) -> str:
        return subprocess.run(
            [path, flag],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
            text=True,
            errors="replace",
            timeout=3,
        ).stdout

    version = re.fullmatch(r"(\d+)\.(\d+)\.(\d+) \(Claude Code\)", probe("--version").strip())
    flags = set(
        re.findall(r"^\s*(?:-[a-zA-Z], )?(--[a-zA-Z][\w-]*)", probe("--help"), re.MULTILINE)
    )
    cli_version = tuple(map(int, version.groups())) if version else ()
    if (
        not cli_version
        or not _REQUIRED_FLAGS <= flags
        or ("--append-system-prompt-file" not in flags and cli_version < _TESTED_CLAUDE_VERSION)
    ):
        raise RunError("incompatible_host", _UNAVAILABLE["incompatible_host"], 503)
    # These aliases and effort levels are supported by the tested Claude Code line.
    efforts = (
        ["low", "medium", "high"]
        if "--effort" in flags and cli_version >= _TESTED_CLAUDE_VERSION
        else []
    )
    models = (
        [
            ModelInfo(id="sonnet", label="Sonnet", efforts=efforts),
            ModelInfo(id="opus", label="Opus", efforts=efforts),
            ModelInfo(id="haiku", label="Haiku", efforts=[]),
        ]
        if "--model" in flags
        else []
    )
    return ProviderInfo(id="claude", label="Claude Code", models=models)


def _installed_claude() -> tuple[str, ProviderInfo]:
    path, reason = availability()
    if path is None:
        reason = reason or "no_host"
        raise RunError(reason, _UNAVAILABLE[reason], 503)
    try:
        path = str(Path(path).absolute())
        stat = Path(path).stat()
        identity = (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size)
        return path, _claude_info(path, identity)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RunError("incompatible_host", _UNAVAILABLE["incompatible_host"], 503) from exc


def discover_providers() -> tuple[list[ProviderInfo], str | None]:
    try:
        _, info = _installed_claude()
    except RunError as exc:
        return [], exc.reason
    return [info], None


def resolve_provider(selection: RunSelection) -> ResolvedProvider:
    if selection.provider != "claude":
        raise RunError("provider_unavailable", "Codex is unavailable for page runs", 503)
    executable, info = _installed_claude()
    models = info.models
    if models is None and (selection.model is not None or selection.effort is not None):
        raise RunError("catalog_unavailable", "Claude Code model options are unavailable", 503)
    model_id = selection.model if selection.model is not None else info.default_model
    model = next((entry for entry in models or [] if entry.id == model_id), None)
    if selection.model is not None and model is None:
        raise RunError("invalid_selection", "Choose an available Claude Code model", 400)
    if selection.effort is not None and (model is None or selection.effort not in model.efforts):
        raise RunError("invalid_selection", "Choose an effort supported by the selected model", 400)
    return ResolvedProvider(executable, selection)
