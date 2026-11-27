"""start.json: a running `groundly start` announces its pid, port and version here, so a stdio
`groundly mcp` can forward model calls to it. It is trusted only as a small regular file owned by
this account that no one else can write, so the port it names is the student's own `start`'s even
when GROUNDLY_HOME sits in a directory another account can write into. Private local state: no
course text, outside `subjects/`, never exported. Windows neither writes nor trusts it (no pid
liveness check there)."""

import json
import os
import stat
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


_MAX_BYTES = 4096
_MAX_PID = 2**31 - 1


def _text() -> str | None:
    """The file's text when it is a small regular file of this account's that no one else can
    write: GROUNDLY_HOME may point at a directory another account can write into."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path(), flags)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022:
            return None
        raw = f.read(_MAX_BYTES + 1)
    return raw.decode("utf-8", "replace") if len(raw) <= _MAX_BYTES else None


def _load() -> StartInfo | None:
    text = _text()
    if text is None:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    pid, port, ver = data.get("pid"), data.get("port"), data.get("version")
    if type(pid) is not int or type(port) is not int or not isinstance(ver, str):
        return None
    # pid 0 and -1 name a group (os.kill succeeds on them); a huge one overflows.
    if not 1 <= port <= 65535 or not 1 <= pid <= _MAX_PID:
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
