import json
import os
import subprocess
from importlib.metadata import version

import pytest

from groundly.core import startfile
from groundly.core.startfile import StartInfo


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path))


def _dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def test_write_then_read_round_trips():
    startfile.write(8765)
    assert startfile.read() == StartInfo(os.getpid(), 8765, version("groundly"))
    assert os.stat(startfile.path()).st_mode & 0o777 == 0o600


def test_read_start_ignores_a_dead_pid():
    payload = {"pid": _dead_pid(), "port": 8765, "version": version("groundly")}
    startfile.path().write_text(json.dumps(payload))
    assert startfile.read() is None


def test_pid_owned_by_another_account_is_not_alive(monkeypatch):
    def refuse(pid, sig):
        raise PermissionError

    monkeypatch.setattr(os, "kill", refuse)
    assert startfile.pid_alive(1) is False


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        json.dumps({"pid": "1", "port": 8765, "version": "x"}),
        json.dumps({"pid": os.getpid(), "port": 0, "version": "x"}),
        json.dumps({"pid": os.getpid(), "port": 70000, "version": "x"}),
        json.dumps({"pid": os.getpid(), "port": 8765}),
        json.dumps({"pid": True, "port": 8765, "version": "x"}),
    ],
)
def test_read_rejects_malformed_files(text):
    startfile.path().write_text(text)
    assert startfile.read() is None


def test_remove_start_keeps_another_pids_runfile():
    payload = {"pid": os.getppid(), "port": 8765, "version": version("groundly")}
    startfile.path().write_text(json.dumps(payload))
    startfile.remove()
    assert startfile.path().exists()

    startfile.write(1)
    startfile.remove()
    assert not startfile.path().exists()


def test_remove_without_a_file_does_not_raise():
    startfile.remove()


def test_windows_writes_and_reads_nothing(monkeypatch):
    monkeypatch.setattr(startfile, "_WINDOWS", True)
    startfile.write(8765)
    assert not startfile.path().exists()
    payload = {"pid": os.getpid(), "port": 8765, "version": version("groundly")}
    startfile.path().write_text(json.dumps(payload))
    assert startfile.read() is None


@pytest.mark.parametrize("pid", [0, -1, 2**63])
def test_read_rejects_pids_that_name_no_single_process(pid):
    # os.kill(0 or -1, 0) signals a group and succeeds; a huge pid overflows.
    payload = {"pid": pid, "port": 8765, "version": version("groundly")}
    startfile.path().write_text(json.dumps(payload))
    assert startfile.read() is None


def _valid() -> str:
    return json.dumps({"pid": os.getpid(), "port": 8765, "version": version("groundly")})


def test_read_ignores_a_symlinked_runfile(tmp_path):
    target = tmp_path / "elsewhere.json"
    target.write_text(_valid())
    startfile.path().symlink_to(target)
    assert startfile.read() is None


def test_read_ignores_a_runfile_others_can_write():
    startfile.path().write_text(_valid())
    os.chmod(startfile.path(), 0o622)
    assert startfile.read() is None


def test_read_ignores_a_runfile_owned_by_another_account(monkeypatch):
    startfile.path().write_text(_valid())
    monkeypatch.setattr(os, "getuid", lambda: os.stat(startfile.path()).st_uid + 1)
    assert startfile.read() is None


def test_read_ignores_a_fifo_without_blocking():
    os.mkfifo(startfile.path())
    assert startfile.read() is None


def test_read_ignores_an_oversized_runfile():
    startfile.path().write_text(_valid() + " " * 8192)
    assert startfile.read() is None
