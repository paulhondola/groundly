"""A page button starts the student's own Claude Code headless. These tests pin what that run
may touch (only Groundly's tools, none of the student's settings) and what the page learns
from it (steps from tool names, never model text). No test calls a model."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from groundly.web import runs
from tests.web.conftest import INIT as FAKE_INIT
from tests.web.conftest import gone, success, use, wait_until_ended


def test_python_dash_m_groundly_runs_the_cli():
    r = subprocess.run(
        [sys.executable, "-m", "groundly", "--help"], check=False, capture_output=True, text=True
    )
    assert r.returncode == 0 and "mcp" in r.stdout


def test_availability_names_why_no_run_can_start(monkeypatch):
    monkeypatch.setattr(runs.shutil, "which", lambda name: None)
    assert runs.availability() == (None, "no_host")
    monkeypatch.setattr(runs.shutil, "which", lambda name: "/bin/claude")
    monkeypatch.setattr(runs.sys, "platform", "win32")
    assert runs.availability() == (None, "unsupported_platform")
    monkeypatch.setattr(runs.sys, "platform", "darwin")
    assert runs.availability() == ("/bin/claude", None)


def test_mcp_config_is_the_running_mcp_or_this_installations_stdio():
    assert runs.mcp_config("http://127.0.0.1:8765/mcp") == {
        "mcpServers": {"groundly": {"type": "http", "url": "http://127.0.0.1:8765/mcp"}}
    }
    assert runs.mcp_config(None) == {
        "mcpServers": {"groundly": {"command": sys.executable, "args": ["-m", "groundly", "mcp"]}}
    }


def test_write_argv_is_isolated_and_allows_only_the_write_tools():
    argv = runs.build_argv("/bin/claude", "write", "P", None)
    assert argv[:3] == ["/bin/claude", "-p", "P"]
    for flag, value in [
        ("--setting-sources", ""),
        ("--tools", ""),
        ("--permission-mode", "dontAsk"),
        ("--output-format", "stream-json"),
    ]:
        assert argv[argv.index(flag) + 1] == value
    for flag in ("--strict-mcp-config", "--verbose", "--no-session-persistence"):
        assert flag in argv
    assert "--model" not in argv
    allowed = argv[argv.index("--allowedTools") + 1].split(",")
    assert set(allowed) == set(runs.WRITE_TOOLS)
    assert not {
        "mcp__groundly__remove_questions",
        "mcp__groundly__submit_tree",
        "mcp__groundly__review_answers",
    } & set(allowed)
    skill = Path(argv[argv.index("--append-system-prompt-file") + 1])
    assert skill.name == "SKILL.md" and "## Grade a test" in skill.read_text()
    assert json.loads(argv[argv.index("--mcp-config") + 1]) == runs.mcp_config(None)


def test_grade_argv_allows_only_the_grade_tools():
    argv = runs.build_argv("/bin/claude", "grade", "P", "http://127.0.0.1:8765/mcp")
    assert set(argv[argv.index("--allowedTools") + 1].split(",")) == set(runs.GRADE_TOOLS)
    assert json.loads(argv[argv.index("--mcp-config") + 1]) == runs.mcp_config(
        "http://127.0.0.1:8765/mcp"
    )


def test_prompts_carry_only_the_flow_the_subject_and_the_topic_id():
    leaf = runs.build_prompt("write", "apd", 27, big=False)
    big = runs.build_prompt("write", "apd", 3, big=True)
    grade = runs.build_prompt("grade", "apd", None, big=False)
    assert '"Write questions for a topic"' in leaf
    assert "topic id 27" in leaf and "10 questions" in leaf
    assert "each subtopic of topic id 3" in big and "fewer than 10" in big
    assert '"Grade a test"' in grade and "`remaining` is 0" in grade
    for p in (leaf, big, grade):
        assert (
            '"apd"' in p and "verified" in p
        )  # the closing line forbids calling anything verified


INIT = {
    "type": "system",
    "subtype": "init",
    "tools": ["mcp__groundly__search", "mcp__groundly__progress"],
    "mcp_servers": [{"name": "groundly", "status": "connected"}],
}


def test_init_check_passes_only_groundly():
    assert runs.check_init(INIT) is None
    assert runs.check_init({**INIT, "tools": INIT["tools"] + ["Bash", "Read"]}) == (
        "isolation_failed",
        "the run had tools other than Groundly's: Bash, Read",
    )
    other = {
        **INIT,
        "mcp_servers": INIT["mcp_servers"] + [{"name": "github", "status": "connected"}],
    }
    assert runs.check_init(other) == (
        "isolation_failed",
        "the run had MCP servers other than Groundly: github",
    )


def test_init_check_names_a_groundly_server_that_did_not_connect():
    failed = {**INIT, "mcp_servers": [{"name": "groundly", "status": "failed"}]}
    assert runs.check_init(failed) == (
        "groundly_not_connected",
        "Groundly's MCP server did not connect (status: failed)",
    )
    assert runs.check_init({**INIT, "mcp_servers": []})[0] == "groundly_not_connected"


def _text(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _use(tool_id, name, tool_input):
    block = {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}
    return {"type": "assistant", "message": {"content": [block]}}


def _result(tool_id, payload, is_error=False):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    block = {
        "type": "tool_result",
        "tool_use_id": tool_id,
        "content": [{"type": "text", "text": text}],
        "is_error": is_error,
    }
    return {"type": "user", "message": {"content": [block]}}


def test_steps_come_from_tool_names_and_counts_never_model_text():
    s = runs.Steps()
    assert s.feed(_text("I will now write <script>")) == []
    assert s.feed(_use("t1", "mcp__groundly__search", {})) == ["Reading the course"]
    assert s.feed(_use("t2", "mcp__groundly__search", {})) == []  # consecutive repeats collapse
    assert s.feed(_use("t3", "mcp__groundly__submit_questions", {"questions": [1, 2, 3]})) == [
        "Submitting 3 questions"
    ]
    assert s.feed(_result("t3", {"accepted": [{}, {}], "rejected": [{}]})) == [
        "2 stored, 1 rejected"
    ]
    assert s.feed(_use("t4", "mcp__groundly__review_answers", {"reviews": [1, 2]})) == [
        "Recording 2 verdicts"
    ]
    assert s.feed(_result("t4", {"recorded": [7, 8], "awaiting": [], "remaining": 0})) == [
        "2 verdicts recorded"
    ]
    assert s.feed(_result("t9", "boom", is_error=True)) == ["A tool call failed"]


def test_steps_read_a_result_wrapped_as_result_or_given_as_a_plain_string():
    s = runs.Steps()
    s.feed(_use("t1", "mcp__groundly__submit_questions", {"questions": [1]}))
    wrapped = _result("t1", {"result": {"accepted": [{}], "rejected": []}})
    wrapped["message"]["content"][0]["content"] = wrapped["message"]["content"][0]["content"][0][
        "text"
    ]
    assert s.feed(wrapped) == ["1 stored, 0 rejected"]


def test_summary_of_success_and_error():
    long = {"type": "result", "subtype": "success", "is_error": False, "result": "x" * 5000}
    assert runs.summary_of(long) == ("x" * runs.MAX_SUMMARY_CHARS, None)
    login = {
        "type": "result",
        "subtype": "success",
        "is_error": True,
        "result": "Please run /login",
    }
    assert runs.summary_of(login) == (None, ("host_error", "Please run /login"))


# --- the Runner: one process, read on a thread, fail closed ---------------------------------


def _start(runner, kind="write"):
    return runner.start(kind, "PDSS", 7, "the prompt")


def test_a_run_reads_steps_and_summary_then_ends_done(fake_claude, tmp_path):
    fake_claude.script([FAKE_INIT, use("t1", "mcp__groundly__search"), success("Stored 10.")])
    runner = runs.Runner()
    started = _start(runner)
    assert started["status"] == "running" and started["kind"] == "write"
    run = wait_until_ended(runner.view)
    assert (run["status"], run["steps"], run["summary"], run["error"]) == (
        "done",
        ["Reading the course"],
        "Stored 10.",
        None,
    )
    (call,) = fake_claude.calls()
    assert Path(call["cwd"]).resolve() == (tmp_path / "home" / ".host-run").resolve()
    assert call["argv"][call["argv"].index("-p") + 1] == "the prompt"


def test_a_second_start_while_running_raises_run_in_progress(fake_claude):
    fake_claude.script([FAKE_INIT, "SLEEP"])
    fake_claude.record_pids()
    runner = runs.Runner()
    _start(runner)
    fake_claude.pids()  # the fake has written its argv
    try:
        with pytest.raises(runs.RunError) as e:
            _start(runner, "grade")
        assert (e.value.reason, e.value.status) == ("run_in_progress", 409)
        assert len(fake_claude.calls()) == 1
    finally:
        runner.shutdown()


def test_no_claude_on_path_raises_no_host(monkeypatch):
    monkeypatch.setattr(runs.shutil, "which", lambda name: None)
    with pytest.raises(runs.RunError) as e:
        _start(runs.Runner())
    assert (e.value.reason, e.value.status) == ("no_host", 503)


def test_bash_in_init_kills_the_run_before_any_turn(fake_claude):
    hostile = {**FAKE_INIT, "tools": FAKE_INIT["tools"] + ["Bash"]}
    fake_claude.script([hostile, "SLEEP", use("t1", "mcp__groundly__search"), success()])
    runner = runs.Runner()
    _start(runner)
    run = wait_until_ended(runner.view)
    assert run["status"] == "failed"
    assert run["error"] == {
        "reason": "isolation_failed",
        "detail": "the run had tools other than Groundly's: Bash",
    }
    assert run["steps"] == [] and run["summary"] is None


def test_a_tool_call_before_init_fails_closed(fake_claude):
    fake_claude.script([use("t1", "mcp__groundly__search"), success()])
    runner = runs.Runner()
    _start(runner)
    run = wait_until_ended(runner.view)
    assert run["status"] == "failed" and run["error"]["reason"] == "isolation_failed"


def test_groundly_not_connected_fails_the_run(fake_claude):
    down = {**FAKE_INIT, "mcp_servers": [{"name": "groundly", "status": "failed"}]}
    fake_claude.script([down, "SLEEP"])
    runner = runs.Runner()
    _start(runner)
    run = wait_until_ended(runner.view)
    assert run["error"]["reason"] == "groundly_not_connected"


def test_exit_without_result_fails_with_code_and_stderr_tail(fake_claude):
    fake_claude.script([FAKE_INIT, use("t1", "mcp__groundly__search")], exit_code=3, stderr="boom")
    runner = runs.Runner()
    _start(runner)
    run = wait_until_ended(runner.view)
    assert run["status"] == "failed"
    assert run["error"] == {"reason": "host_exit", "detail": "claude exited with code 3: boom"}
    assert run["steps"] == ["Reading the course"]


def test_a_login_error_result_is_the_error_shown(fake_claude):
    login = {
        "type": "result",
        "subtype": "success",
        "is_error": True,
        "result": "Please run /login",
    }
    fake_claude.script([FAKE_INIT, login], exit_code=1)
    runner = runs.Runner()
    _start(runner)
    run = wait_until_ended(runner.view)
    assert run["status"] == "failed"
    assert run["error"] == {"reason": "host_error", "detail": "Please run /login"}


def test_junk_and_oversized_lines_are_skipped(fake_claude):
    lines = [FAKE_INIT, "not json", "x" * 3_000_000, "[1, 2]"]
    for i in range(200):
        lines.append(use(f"s{i}", "mcp__groundly__search"))
        lines.append(use(f"g{i}", "mcp__groundly__get_page"))
    fake_claude.script([*lines, success("Done.")])
    runner = runs.Runner()
    _start(runner)
    run = wait_until_ended(runner.view)
    assert run["status"] == "done" and run["summary"] == "Done."
    assert len(run["steps"]) == runs.MAX_STEPS


def test_stop_ends_running_as_stopped(fake_claude):
    fake_claude.script([FAKE_INIT, use("t1", "mcp__groundly__search"), "SLEEP"])
    fake_claude.record_pids()
    runner = runs.Runner()
    _start(runner)
    claude_pid, _ = fake_claude.pids()
    stopped = runner.stop()
    assert stopped["status"] == "stopped"
    assert gone(claude_pid)
    assert wait_until_ended(runner.view)["status"] == "stopped"


def test_stop_with_nothing_running_returns_the_last_run(fake_claude):
    runner = runs.Runner()
    assert runner.stop() is None
    _start(runner)
    done = wait_until_ended(runner.view)
    assert runner.stop() == done


def test_timeout_fails_naming_the_timeout(fake_claude, monkeypatch):
    monkeypatch.setattr(runs, "RUN_TIMEOUT_S", 0.5)
    fake_claude.script([FAKE_INIT, "SLEEP"])
    runner = runs.Runner()
    _start(runner)
    run = wait_until_ended(runner.view)
    assert run["status"] == "failed" and run["error"]["reason"] == "timeout"
    assert "limit" in run["error"]["detail"]


def test_shutdown_kills_claude_and_its_children(fake_claude):
    fake_claude.script([FAKE_INIT, "SLEEP"])
    fake_claude.record_pids()
    runner = runs.Runner()
    _start(runner)
    claude_pid, child_pid = fake_claude.pids()
    runner.shutdown()
    assert gone(claude_pid) and gone(child_pid)
