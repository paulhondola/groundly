import os
import subprocess
import sys

import pytest

from bench.common import sampler

IOREG_SAMPLE = (
    '"PerformanceStatistics" = {"In use system memory (driver)"=0,'
    '"Alloc system memory"=4498669568,"Device Utilization %"=27,'
    '"In use system memory"=1037664256}'
)
ALLOC_200MB_AND_SPIN_1S = (
    "import time\nb = b'x' * (200 << 20)\nt = time.time()\nwhile time.time() - t < 1.5:\n    pass\n"
)


def test_parse_ioreg_reads_in_use_memory():
    assert sampler.parse_ioreg(IOREG_SAMPLE) == 1037664256
    assert sampler.parse_ioreg("nothing here") is None


@pytest.mark.skipif(sys.platform != "darwin", reason="libproc")
def test_footprint_of_this_process_is_positive():
    assert sampler.footprint_bytes(os.getpid()) > 0


def test_footprint_of_a_missing_pid_is_none():
    assert sampler.footprint_bytes(2**22 + 12345) is None


def test_sampler_sees_a_child_allocate_and_burn_cpu():
    child = subprocess.Popen([sys.executable, "-c", ALLOC_200MB_AND_SPIN_1S])
    s = sampler.ProcSampler(child.pid)
    s.start()
    child.wait()
    rows = s.stop()
    assert len(rows) >= 5
    assert max(r["rss_mb"] for r in rows) >= 200
    assert rows[-1]["cpu_s"] >= rows[0]["cpu_s"]
    assert all(rows[i]["t_s"] < rows[i + 1]["t_s"] for i in range(len(rows) - 1))
    assert child.pid in s.pids
