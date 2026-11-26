import os
import subprocess
import sys
from pathlib import Path

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


POWER_SAMPLE = (Path(__file__).parent / "powermetrics.plist").read_bytes()


def test_parse_plists_reads_both_samples():
    assert len(sampler.parse_plists(POWER_SAMPLE)) == 2
    cut = POWER_SAMPLE[: POWER_SAMPLE.index(b"\0") + 200]
    assert len(sampler.parse_plists(cut)) == 1


def test_power_rows_keeps_only_the_tree():
    samples = sampler.parse_plists(POWER_SAMPLE)
    system, tasks = sampler.power_rows(samples, 0, {415})
    assert len(system) == 2 and all(r["combined_mw"] > 0 for r in system)
    assert system[1]["t_s"] - system[0]["t_s"] == pytest.approx(1)  # timestamps are whole seconds
    assert system[0]["gpu_active"] == pytest.approx(1 - 0.626961)
    assert system[0]["gpu_freq_mhz"] == pytest.approx(422.392)
    assert {r["pid"] for r in tasks} == {415} and len(tasks) == 2
    assert all(r["gpu_ms_per_s"] is None for r in tasks)  # this macOS writes no per-task GPU
    assert tasks[0]["cpu_ms_per_s"] >= 0 and tasks[0]["energy_impact"] is not None


def test_power_session_refuses_without_sudo(monkeypatch):
    monkeypatch.setattr(sampler, "sudo_ok", lambda: False)
    message = r'powermetrics needs sudo — run "sudo -v" first, or pass --no-power'
    with pytest.raises(SystemExit, match=message), sampler.PowerSession():
        pass


@pytest.mark.slow
@pytest.mark.skipif(not sampler.sudo_ok(), reason="needs sudo -n")
def test_power_session_records_while_alive():
    import time

    with sampler.PowerSession() as power:
        t = time.time()
        time.sleep(4)  # ~1.5 s to the first sample (sudo + one interval), then 2 Hz
        assert power.alive()
        system, _ = power.slice(t - 1, time.time() + 1, set())
    assert len(system) >= 4


def _fake_sudo(tmp_path, monkeypatch, script):
    fake = tmp_path / "sudo"
    fake.write_text("#!/bin/sh\n" + script)
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")


def test_sudo_ok_accepts_a_rule_limited_to_powermetrics(tmp_path, monkeypatch):
    # Like a NOPASSWD rule for powermetrics alone: any other command is refused.
    _fake_sudo(
        tmp_path, monkeypatch, '[ "$1 $2" = "-n /usr/bin/powermetrics" ] && exit 0\nexit 1\n'
    )
    assert sampler.sudo_ok()


def test_sudo_ok_refuses_when_listing_allows_but_running_needs_a_password(tmp_path, monkeypatch):
    # Like `(ALL) ALL` with no cached `sudo -v`: `sudo -n -l` says yes, running asks for a password.
    _fake_sudo(tmp_path, monkeypatch, '[ "$2" = "-l" ] && exit 0\nexit 1\n')
    assert not sampler.sudo_ok()


def test_power_session_restarts_powermetrics_when_it_dies(tmp_path, monkeypatch):
    # powermetrics can crash mid-run ("task <name> is invalid" in its tasks sampler); a fake that
    # writes the captured samples and exits stands in for one that dies every 0.3 s.
    _fake_sudo(
        tmp_path, monkeypatch, f'cat "{Path(__file__).parent / "powermetrics.plist"}"\nsleep 0.3\n'
    )
    import time

    with sampler.PowerSession() as power:
        time.sleep(1.5)
        assert power.alive()
        system, _ = power.slice(0, time.time() + 60, set())
    assert len(system) > 2  # more than the one launch's two samples
    assert "powermetrics restarted" in (power.note or "")


# sudo_ok's one-sample probe returns at once; anything but powermetrics (e.g. `sudo -n kill`) fails
PROBE_OK = '[ "$2" = /usr/bin/powermetrics ] || exit 1\n[ "$4" = cpu_power ] && exit 0\n'


def _fixture_cmd():
    return f'cat "{Path(__file__).parent / "powermetrics.plist"}"'


def test_the_first_sample_is_stamped_when_it_arrives(tmp_path, monkeypatch):
    # powermetrics' start-up is not in elapsed_ns: launch + elapsed_ns would stamp it early.
    _fake_sudo(tmp_path, monkeypatch, f"{PROBE_OK}sleep 1\n{_fixture_cmd()}\nsleep 5\n")
    import time

    with sampler.PowerSession() as power:
        launch = time.time()
        assert power.wait_past(launch + 0.5, timeout=5)
        first = power._samples[0][0]
    assert first >= launch + 0.9


def test_wait_past_waits_for_a_sample_beyond_the_run(tmp_path, monkeypatch):
    # One NUL between documents, as powermetrics writes; ends on SIGPIPE, like powermetrics.
    loop = f"while {_fixture_cmd()} && printf '\\0'; do sleep 0.3; done\n"
    _fake_sudo(tmp_path, monkeypatch, PROBE_OK + loop)
    import time

    with sampler.PowerSession() as power:
        t = time.time() + 0.5
        assert power.wait_past(t, timeout=5)
        assert power._samples[-1][0] >= t
        start = time.time()
        assert not power.wait_past(time.time() + 1000, timeout=0.5)
        assert time.time() - start < 2
