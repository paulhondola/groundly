"""Wall time and peak process-tree RSS of one command.

Peak RSS is the largest sum of `rss` over the command and all its descendants at
one sampling instant, so a parent plus worker children counts together. A
process that exits between listing and reading is skipped, never fatal.

With `memray_out`, the command runs under `memray run --follow-fork`: forked
children are traced, an `exec`'d process (the extraction worker) is not.
`cmd[0]` must then be a Python script path.
"""

import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import psutil


@dataclass(frozen=True)
class Measurement:
    wall_s: float
    peak_rss_mb: float
    returncode: int


def _tree_rss(root: psutil.Process) -> int:
    total = 0
    try:
        procs = [root, *root.children(recursive=True)]
    except psutil.Error:
        return 0
    for p in procs:
        try:
            total += p.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return total


def _command(cmd: list[str], memray_out: Path | None, memray_python: Path | None) -> list[str]:
    if memray_out is None:
        return cmd
    py = str(memray_python or sys.executable)
    return [py, "-m", "memray", "run", "--follow-fork", "-o", str(memray_out), *cmd]


def run(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    interval: float = 0.1,
    memray_out: Path | None = None,
    memray_python: Path | None = None,
) -> Measurement:
    cmd = _command(cmd, memray_out, memray_python)
    start = time.perf_counter()
    proc = subprocess.Popen(cmd, env=env, cwd=cwd)
    root = psutil.Process(proc.pid)
    peak = 0
    while proc.poll() is None:
        peak = max(peak, _tree_rss(root))
        time.sleep(interval)
    wall = time.perf_counter() - start
    return Measurement(wall, peak / (1024 * 1024), proc.returncode)
