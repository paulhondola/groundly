"""One measured command: wall time, return code, and its samplers' rows.

`ProcSampler` samples the command's process tree for the whole run; peak RSS is the largest
tree-summed RSS among its rows. With a `PowerSession`, the run also takes the power and
per-task rows inside its own time window, tasks filtered to every pid the tree ever had.
A sampler that could not read leaves its rows empty and says why in `sampler_note`.

With `memray_out`, the command runs under `memray run --follow-fork`: forked
children are traced, an `exec`'d process (the extraction worker) is not.
`cmd` must then start with a Python script path or `-m module`.
"""

import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from bench.common.sampler import PowerSession, ProcSampler


@dataclass(frozen=True)
class Measurement:
    wall_s: float
    peak_rss_mb: float
    returncode: int
    started_at: datetime
    ended_at: datetime
    samples: dict[str, list[dict]]  # sample_proc, sample_power, sample_task
    sampler_note: str | None


def run_columns(m: Measurement, idle_mw: float | None, power: PowerSession | None) -> dict:
    """The `run` columns every bench fills the same way from one measurement."""
    return {
        "started_at": m.started_at.replace(tzinfo=None),  # stored as naive UTC
        "ended_at": m.ended_at.replace(tzinfo=None),
        "returncode": m.returncode,
        "wall_s": m.wall_s,
        "idle_combined_mw": idle_mw,
        "power_sampler": "powermetrics" if power is not None else "none",
        "sampler_note": m.sampler_note,
    }


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
    power: PowerSession | None = None,
) -> Measurement:
    cmd = _command(cmd, memray_out, memray_python)
    started_at, t_start = datetime.now(UTC), time.time()
    start = time.perf_counter()
    proc = subprocess.Popen(cmd, env=env, cwd=cwd)
    sampler = ProcSampler(proc.pid, interval)
    sampler.start()
    proc.wait()
    wall = time.perf_counter() - start
    proc_rows = sampler.stop()
    ended_at, t_end = datetime.now(UTC), time.time()
    notes = [sampler.note]
    power_rows: list[dict] = []
    task_rows: list[dict] = []
    if power is not None:
        power.wait_past(t_end)  # the sample covering the run's last moments arrives a beat later
        if not power.alive():
            notes.append("powermetrics not running")
        power_rows, task_rows = power.slice(t_start, t_end, sampler.pids | {proc.pid})
        notes.append(power.note)
    return Measurement(
        wall_s=wall,
        peak_rss_mb=max((r["rss_mb"] for r in proc_rows), default=0.0),
        returncode=proc.returncode,
        started_at=started_at,
        ended_at=ended_at,
        samples={"sample_proc": proc_rows, "sample_power": power_rows, "sample_task": task_rows},
        sampler_note="; ".join(dict.fromkeys(n for n in notes if n)) or None,
    )
