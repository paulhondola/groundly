import sys

from bench.common.monitor import run

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
