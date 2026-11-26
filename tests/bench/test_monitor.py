import os
import sys

from bench.common.monitor import _command, run

PARENT_AND_CHILD_150MB = """
import subprocess, sys, time
a = bytearray(150 * 1024 * 1024)
a[::4096] = b"x" * len(a[::4096])
child = "import time; a = bytearray(150*1024*1024); a[::4096] = b'x'*len(a[::4096]); time.sleep(1)"
p = subprocess.Popen([sys.executable, "-c", child])
time.sleep(1)
p.wait()
"""

SPAWN_50_SHORT_CHILDREN = """
import subprocess, sys, time
for _ in range(50):
    subprocess.Popen([sys.executable, "-c", "pass"])
    time.sleep(0.02)
"""


def test_peak_sums_the_process_tree():
    m = run([sys.executable, "-c", PARENT_AND_CHILD_150MB])
    assert m.returncode == 0
    assert m.peak_rss_mb >= 280


def test_short_lived_children_do_not_crash_the_sampler():
    m = run([sys.executable, "-c", SPAWN_50_SHORT_CHILDREN], interval=0.01)
    assert m.returncode == 0


def test_nonzero_exit_is_reported():
    assert run([sys.executable, "-c", "raise SystemExit(3)"]).returncode == 3


def test_memray_wraps_the_command_with_the_given_python(tmp_path):
    fake = tmp_path / "fakepython"
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$ARGV_OUT"\n')
    fake.chmod(0o755)
    argv_out = tmp_path / "argv.txt"
    out = tmp_path / "x.bin"
    m = run(
        ["script.py", "arg"],
        env={**os.environ, "ARGV_OUT": str(argv_out)},
        memray_out=out,
        memray_python=fake,
    )
    assert m.returncode == 0
    assert argv_out.read_text().split() == [
        "-m", "memray", "run", "--follow-fork", "-o", str(out), "script.py", "arg",
    ]  # fmt: skip


def test_memray_python_defaults_to_the_current_interpreter(tmp_path):
    out = tmp_path / "x.bin"
    assert _command(["s.py"], out, None)[0] == sys.executable
    assert _command(["s.py"], None, None) == ["s.py"]


def test_measurement_carries_proc_samples():
    m = run([sys.executable, "-c", "import time; time.sleep(1)"])
    assert len(m.samples["sample_proc"]) >= 5
    assert m.started_at < m.ended_at
    assert m.samples["sample_power"] == [] and m.samples["sample_task"] == []


def test_instant_child_writes_no_samples_and_does_not_crash():
    m = run([sys.executable, "-c", "pass"])
    assert m.returncode == 0
    assert len(m.samples["sample_proc"]) <= 2  # one at start, a second if startup is slow
    assert m.peak_rss_mb >= 0


class DeadPower:
    note = None

    def alive(self):
        return False

    def wait_past(self, t, timeout=2.0):
        return False

    def slice(self, t_start, t_end, pids):
        return [], []


def test_dead_power_session_leaves_a_note():
    m = run([sys.executable, "-c", "pass"], power=DeadPower())
    assert m.samples["sample_power"] == []
    assert "powermetrics" in (m.sampler_note or "")
