"""Samplers for one measured child process tree.

`ProcSampler` reads, 10 times a second, over the child and all its descendants: summed RSS,
summed physical footprint (what Activity Monitor calls Memory; unlike RSS it counts Metal and
IOKit memory, where MLX keeps its weights), and summed CPU seconds. CPU seconds of a descendant
that has already exited are lost (psutil cannot read a reaped process), so a tree whose workers
come and go under-reports CPU. System-wide GPU memory in use is read from `ioreg` once a second,
on every tenth row; the other rows leave it NULL by design, since spawning `ioreg` at 10 Hz
would itself load the CPU being measured.

A value that cannot be read is None, never 0, and `note` says why.
"""

import ctypes
import ctypes.util
import re
import subprocess
import sys
import threading
import time

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
