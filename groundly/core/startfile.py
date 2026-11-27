"""start.json: a running `groundly start` announces its pid, port and version here, so a stdio
`groundly mcp` can forward model calls to it. Only the student's own account can write their home,
so the port it names is their `start`'s, never another local user's. Private local state: no
course text, outside `subjects/`, never exported. Windows neither writes nor trusts it (no pid
liveness check there)."""

import json
import os
import tempfile
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

from groundly.core.paths import groundly_home

VERSION = version("groundly")
_WINDOWS = os.name == "nt"


@dataclass(frozen=True)
class StartInfo:
    pid: int
    port: int
    version: str


def path() -> Path:
    return groundly_home() / "start.json"


def pid_alive(pid: int) -> bool:
    """`start` runs as the student, so a pid only another account may signal is a reused pid."""
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def write(port: int) -> None:
    if _WINDOWS:
        return
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".start-")  # mode 0600
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"pid": os.getpid(), "port": port, "version": VERSION}, f)
        os.replace(tmp, target)
    except BaseException:
        os.unlink(tmp)
        raise


def _load() -> StartInfo | None:
    try:
        data = json.loads(path().read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    pid, port, ver = data.get("pid"), data.get("port"), data.get("version")
    if type(pid) is not int or type(port) is not int or not isinstance(ver, str):
        return None
    if not 1 <= port <= 65535:
        return None
    return StartInfo(pid, port, ver)


def read() -> StartInfo | None:
    """The live `start` the file names, or None: missing, malformed, or its pid is gone."""
    if _WINDOWS:
        return None
    info = _load()
    return info if info is not None and pid_alive(info.pid) else None


def remove() -> None:
    """Delete the file if it still names this process: a later `start` may have replaced it."""
    info = _load()
    if info is None or info.pid != os.getpid():
        return
    try:
        path().unlink()
    except OSError:
        pass
