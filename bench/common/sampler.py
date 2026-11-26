"""Samplers for one measured child process tree.

`ProcSampler` reads, 10 times a second, over the child and all its descendants: summed RSS,
summed physical footprint (what Activity Monitor calls Memory; unlike RSS it counts Metal and
IOKit memory, where MLX keeps its weights), and summed CPU seconds. CPU seconds of a descendant
that has already exited are lost (psutil cannot read a reaped process), so a tree whose workers
come and go under-reports CPU. System-wide GPU memory in use is read from `ioreg` once a second,
on every tenth row; the other rows leave it NULL by design, since spawning `ioreg` at 10 Hz
would itself load the CPU being measured.

`PowerSession` runs one `sudo -n powermetrics` for a whole bench invocation (2 Hz) and keeps,
per sample, system CPU/GPU/ANE power, GPU active ratio and frequency, and per-process CPU time
and energy impact; each run takes the samples inside its own time window. It reads
powermetrics from a pipe, so closing the pipe ends it: the harness never needs a second sudo
command (a sudoers rule limited to powermetrics is enough). Per-process GPU time is NULL where
this macOS's powermetrics does not report it.

A value that cannot be read is None, never 0, and `note` says why.
"""

import contextlib
import ctypes
import ctypes.util
import os
import plistlib
import re
import statistics
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from datetime import UTC
from typing import Self
from xml.parsers.expat import ExpatError

import psutil

_MB = 1024 * 1024
_IOREG_EVERY = 10
_IN_USE = re.compile(r'"In use system memory"=(\d+)')

_RUSAGE_INFO_V2 = 2
_FOOTPRINT_SLOT = (
    2 + 7
)  # rusage_info_v2: 16-byte uuid, then u64 fields; ri_phys_footprint is the 8th
_libproc = ctypes.CDLL(ctypes.util.find_library("proc")) if sys.platform == "darwin" else None


def footprint_bytes(pid: int) -> int | None:
    """A process's physical footprint from libproc, or None off macOS or on any error."""
    if _libproc is None:
        return None
    buf = (ctypes.c_uint64 * 64)()
    if _libproc.proc_pid_rusage(ctypes.c_int(pid), _RUSAGE_INFO_V2, ctypes.byref(buf)) != 0:
        return None
    return int(buf[_FOOTPRINT_SLOT])


def parse_ioreg(text: str) -> int | None:
    """Bytes of system memory the GPU holds, from `ioreg -c IOAccelerator` output."""
    m = _IN_USE.search(text)
    return int(m.group(1)) if m else None


def gpu_mem_system_bytes() -> int | None:
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.run(
            ["ioreg", "-r", "-d", "1", "-c", "IOAccelerator"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_ioreg(out)


class ProcSampler:
    """Samples a process tree on a daemon thread until `stop()`; `pids` holds every pid seen."""

    def __init__(self, root_pid: int, interval: float = 0.1) -> None:
        self.root_pid = root_pid
        self.interval = interval
        self.pids: set[int] = set()
        self.note: str | None = None
        self._rows: list[dict] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._t0 = 0.0

    def start(self) -> None:
        self._t0 = time.perf_counter()
        self._thread.start()

    def stop(self) -> list[dict]:
        self._stop.set()
        self._thread.join()
        return self._rows

    def _tree(self) -> list[psutil.Process]:
        try:
            root = psutil.Process(self.root_pid)
            return [root, *root.children(recursive=True)]
        except psutil.Error:
            return []

    def _sample(self, tick: int) -> dict | None:
        procs = self._tree()
        if not procs:
            return None
        rss = cpu = 0.0
        footprint: float | None = 0.0
        for p in procs:
            try:
                rss += p.memory_info().rss
                times = p.cpu_times()
                cpu += times.user + times.system
            except psutil.Error:
                continue
            self.pids.add(p.pid)
            fp = footprint_bytes(p.pid)
            if fp is None:
                footprint = None
            elif footprint is not None:
                footprint += fp
        if footprint is None and self.note is None:
            self.note = "footprint unavailable (libproc)"
        gpu = gpu_mem_system_bytes() if tick % _IOREG_EVERY == 0 else None
        return {
            "t_s": time.perf_counter() - self._t0,
            "rss_mb": rss / _MB,
            "footprint_mb": None if footprint is None else footprint / _MB,
            "cpu_s": cpu,
            "gpu_mem_mb_system": None if gpu is None else gpu / _MB,
        }

    def _loop(self) -> None:
        tick = 0
        while not self._stop.is_set():
            row = self._sample(tick)
            if row is not None:
                self._rows.append(row)
            tick += 1
            self._stop.wait(self.interval)


NO_SUDO = 'powermetrics needs sudo — run "sudo -v" first, or pass --no-power'
POWERMETRICS = [
    "sudo", "-n", "/usr/bin/powermetrics",
    "--samplers", "cpu_power,gpu_power,ane_power,tasks",
    "--show-process-gpu", "--show-process-energy", "-i", "500", "-f", "plist",
]  # fmt: skip
_POWER_KEYS = ("cpu_power", "gpu_power", "ane_power", "combined_power")
_TASK_KEYS = ("pid", "name", "cputime_ms_per_s", "gputime_ms_per_s", "energy_impact_per_s")


def sudo_ok() -> bool:
    """True when sudo runs without a prompt in this shell (a cached `sudo -v` or a sudoers rule)."""
    try:
        return (
            subprocess.run(["sudo", "-n", "true"], capture_output=True, check=False).returncode == 0
        )
    except OSError:
        return False


def parse_plists(data: bytes) -> list[dict]:
    """powermetrics' `-f plist` stream: NUL-separated plist documents; a partial one is dropped."""
    out = []
    for doc in data.split(b"\0"):
        if doc.strip():
            with contextlib.suppress(ExpatError, plistlib.InvalidFileException, ValueError):
                out.append(plistlib.loads(doc.strip()))  # a partial document fails to parse
    return out


def power_rows(
    samples: list[dict], t0: float, pids: set[int], times: list[float] | None = None
) -> tuple[list[dict], list[dict]]:
    """`sample_power` rows, and `sample_task` rows for `pids` only; `t_s` is relative to `t0`.

    `times` are the samples' end times (epoch seconds); without them, each sample's own
    `timestamp` is used, which powermetrics writes to the whole second only.
    """
    if times is None:
        times = [s["timestamp"].replace(tzinfo=UTC).timestamp() for s in samples]
    system, tasks = [], []
    for s, t in zip(samples, times, strict=True):
        p, g = s.get("processor", {}), s.get("gpu", {})
        idle = g.get("idle_ratio")
        system.append(
            {"t_s": t - t0, "cpu_mw": p.get("cpu_power"), "gpu_mw": p.get("gpu_power"),
             "ane_mw": p.get("ane_power"), "combined_mw": p.get("combined_power"),
             "gpu_active": None if idle is None else 1 - idle,
             # named freq_hz by powermetrics, but the value is in MHz
             "gpu_freq_mhz": g.get("freq_hz")}
        )  # fmt: skip
        tasks += [
            {"t_s": t - t0, "pid": k["pid"], "name": k.get("name"),
             "cpu_ms_per_s": k.get("cputime_ms_per_s"), "gpu_ms_per_s": k.get("gputime_ms_per_s"),
             "energy_impact": k.get("energy_impact_per_s")}
            for k in s.get("tasks", []) if k.get("pid") in pids
        ]  # fmt: skip
    return system, tasks


def _compact(s: dict) -> dict:
    """Only the fields `power_rows` reads: a full sample is ~150 KB, this is ~10 KB."""
    p, g = s.get("processor", {}), s.get("gpu", {})
    return {
        "elapsed_ns": s.get("elapsed_ns", 0),
        "processor": {k: p[k] for k in _POWER_KEYS if k in p},
        "gpu": {k: g[k] for k in ("freq_hz", "idle_ratio") if k in g},
        "tasks": [{k: t[k] for k in _TASK_KEYS if k in t} for t in s.get("tasks", [])],
    }


@contextlib.contextmanager
def power_session(no_power: bool) -> Iterator["PowerSession | None"]:
    """A `PowerSession` for one bench invocation, or None with `--no-power`."""
    if no_power:
        yield None
        return
    with PowerSession() as power:
        yield power


class PowerSession:
    """One powermetrics process for a bench invocation; refuses to start without sudo.

    Samples are timed from launch plus the summed `elapsed_ns` of the samples before them.
    ponytail: every sample stays in memory (~20 KB/s); spill to disk if a session runs for hours.
    """

    def __init__(self) -> None:
        self.note: str | None = None
        self._samples: list[tuple[float, dict]] = []  # (end time, compact sample)
        self._stop = threading.Event()
        self._idle: float | None = None

    def __enter__(self) -> Self:
        if not sudo_ok():
            raise SystemExit(NO_SUDO)
        import tempfile

        self._err = tempfile.TemporaryFile()
        self._launch = time.time()
        self._proc = subprocess.Popen(
            POWERMETRICS, stdout=subprocess.PIPE, stderr=self._err, start_new_session=True
        )
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        return self

    def _read(self) -> None:
        assert self._proc.stdout is not None
        fd, buf, clock = self._proc.stdout.fileno(), b"", self._launch
        gpu_seen = False
        while not self._stop.is_set():
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                break
            *docs, buf = (buf + chunk).split(b"\0")
            for s in parse_plists(b"\0".join(docs)):
                clock += s.get("elapsed_ns", 0) / 1e9
                c = _compact(s)
                gpu_seen = gpu_seen or any("gputime_ms_per_s" in t for t in c["tasks"])
                self._samples.append((clock, c))
                if not gpu_seen and self.note is None:
                    self.note = "per-process GPU time not reported by powermetrics on this macOS"
        self._proc.stdout.close()  # powermetrics gets SIGPIPE on its next write and exits

    def alive(self) -> bool:
        return self._proc.poll() is None

    def idle_mw(self, seconds: float = 10) -> float | None:
        """Mean combined power over the next `seconds` with nothing measured; call right after entry."""
        t = time.time()
        time.sleep(seconds)
        system, _ = self.slice(t, time.time(), set())
        vals = [r["combined_mw"] for r in system if r["combined_mw"] is not None]
        self._idle = statistics.fmean(vals) if vals else None
        if self._idle is None:
            self.note = "no powermetrics samples during the idle baseline"
        return self._idle

    def slice(self, t_start: float, t_end: float, pids: set[int]) -> tuple[list[dict], list[dict]]:
        """Rows for samples whose interval midpoint lies in [t_start, t_end]."""
        picked = [
            (t, s) for t, s in list(self._samples)
            if t_start <= t - s["elapsed_ns"] / 2e9 <= t_end
        ]  # fmt: skip
        if not self.alive() and self.note is None:
            self.note = f"powermetrics exited (rc {self._proc.returncode})"
        return power_rows([s for _, s in picked], t_start, pids, [t for t, _ in picked])

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._reader.join(timeout=5)
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # SIGPIPE did not end it (root process: this user cannot signal it directly)
            subprocess.run(["sudo", "-n", "kill", str(self._proc.pid)], check=False)
        self._err.close()
