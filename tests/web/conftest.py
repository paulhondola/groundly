"""A fake `claude` on PATH: replays a scripted stream-json run so no test calls a model."""

import json
import os
import sys
import time
from pathlib import Path

import pytest

_FAKE = """#!{python}
import json, os, subprocess, sys, time
with open(os.environ["FAKE_CLAUDE_ARGV"], "a") as f:
    f.write(json.dumps({{"argv": sys.argv, "cwd": os.getcwd()}}) + "\\n")
if os.environ.get("FAKE_CLAUDE_PIDS"):
    child = subprocess.Popen(["sleep", "60"], stdout=subprocess.DEVNULL)  # own pipe, as claude gives its MCP server
    with open(os.environ["FAKE_CLAUDE_PIDS"], "w") as f:
        f.write(f"{{os.getpid()}} {{child.pid}}")
for line in open(os.environ["FAKE_CLAUDE_SCRIPT"]).read().splitlines():
    if line == "SLEEP":
        time.sleep(30)
        continue
    print(line, flush=True)
sys.stderr.write(os.environ.get("FAKE_CLAUDE_STDERR", ""))
sys.exit(int(os.environ.get("FAKE_CLAUDE_EXIT", "0")))
"""

INIT = {
    "type": "system",
    "subtype": "init",
    "tools": ["mcp__groundly__search", "mcp__groundly__submit_questions"],
    "mcp_servers": [{"name": "groundly", "status": "connected"}],
}


def success(text="Stored 10 questions."):
    return {"type": "result", "subtype": "success", "is_error": False, "result": text}


def use(tool_id, name, tool_input=None):
    block = {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input or {}}
    return {"type": "assistant", "message": {"content": [block]}}


class FakeClaude:
    def __init__(self, root: Path, monkeypatch):
        self.root = root
        self.monkeypatch = monkeypatch
        self.calls_file = root / "argv.jsonl"
        self.script_file = root / "script.txt"
        self.pids_file = root / "pids.txt"
        monkeypatch.setenv("FAKE_CLAUDE_ARGV", str(self.calls_file))
        monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(self.script_file))
        self.script([INIT, success()])

    def script(self, lines, *, exit_code=0, stderr=""):
        """Lines are events (dicts), raw strings, or "SLEEP" to hang for 30 s."""
        text = "\n".join(json.dumps(x) if isinstance(x, dict) else x for x in lines)
        self.script_file.write_text(text + "\n")
        self.monkeypatch.setenv("FAKE_CLAUDE_EXIT", str(exit_code))
        self.monkeypatch.setenv("FAKE_CLAUDE_STDERR", stderr)

    def record_pids(self):
        self.monkeypatch.setenv("FAKE_CLAUDE_PIDS", str(self.pids_file))

    def pids(self) -> list[int]:
        deadline = time.monotonic() + 10
        while not self.pids_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        return [int(p) for p in self.pids_file.read_text().split()]

    def calls(self) -> list[dict]:
        if not self.calls_file.exists():
            return []
        return [json.loads(line) for line in self.calls_file.read_text().splitlines()]

    def prompt(self) -> str:
        argv = self.calls()[-1]["argv"]
        return argv[argv.index("-p") + 1]


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    """A `claude` first on PATH, and GROUNDLY_HOME in tmp_path (where the run dir goes)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "claude"
    exe.write_text(_FAKE.format(python=sys.executable))
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)
    return FakeClaude(tmp_path, monkeypatch)


def wait_until_ended(view, timeout=10.0) -> dict:
    """Poll a run view callable until its status leaves `running`."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = view()
        if run and run["status"] != "running":
            return run
        time.sleep(0.05)
    raise AssertionError(f"run still running after {timeout}s: {view()}")


def gone(pid: int, timeout=5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False
