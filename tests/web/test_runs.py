"""A page button starts the student's own Claude Code headless. These tests pin what that run
may touch (only Groundly's tools, none of the student's settings) and what the page learns
from it (steps from tool names, never model text). No test calls a model."""

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from groundly.web import runs
from tests.web.conftest import INIT as FAKE_INIT
from tests.web.conftest import (
    gone,
    stream_delta,
    stream_start,
    success,
    tool_result,
    use,
    wait_until_ended,
)


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
        "mcpServers": {
            "groundly": {"command": sys.executable, "args": ["-P", "-m", "groundly", "mcp"]}
        }
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
    assert skill.parent.name == "groundly-questions" and skill.name == "SKILL.md"
    assert "name: groundly-questions" in skill.read_text()
    assert "mcp__groundly__topic" in allowed
    assert json.loads(argv[argv.index("--mcp-config") + 1]) == runs.mcp_config(None)


def test_grade_argv_allows_only_the_grade_tools():
    argv = runs.build_argv("/bin/claude", "grade", "P", "http://127.0.0.1:8765/mcp")
    assert set(argv[argv.index("--allowedTools") + 1].split(",")) == set(runs.GRADE_TOOLS)
    skill = Path(argv[argv.index("--append-system-prompt-file") + 1])
    assert skill.parent.name == "groundly-grade" and "name: groundly-grade" in skill.read_text()
    assert "mcp__groundly__topic" not in runs.GRADE_TOOLS
    assert json.loads(argv[argv.index("--mcp-config") + 1]) == runs.mcp_config(
        "http://127.0.0.1:8765/mcp"
    )


def test_prompts_carry_only_the_flow_the_subject_and_the_topic_id():
    leaf = runs.build_prompt("write", "apd", 27, big=False)
    big = runs.build_prompt("write", "apd", 3, big=True)
    grade = runs.build_prompt("grade", "apd", None, big=False)
    assert "groundly-questions" in leaf and "groundly-questions" in big
    assert "topic id 27" in leaf and "10 questions" in leaf
    assert "each subtopic of topic id 3" in big and "fewer than 10" in big
    assert "groundly-grade" in grade and "`remaining` is 0" in grade
    for p in (leaf, big, grade):
        assert (
            '"apd"' in p and "verified" in p
        )  # the closing line forbids calling anything verified
        assert p.startswith("This is a page run.")  # the skills' page-run branches key on it


def test_reading_a_topic_is_a_plain_word_step():
    assert runs.Steps().feed(use("t1", "mcp__groundly__topic", {"topic_id": 27})) == (
        ["Reading the topic"],
        False,
    )


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


@pytest.mark.parametrize(
    "init",
    [
        {k: v for k, v in INIT.items() if k != "tools"},
        {**INIT, "tools": None},
        {k: v for k, v in INIT.items() if k != "mcp_servers"},
        {**INIT, "mcp_servers": ["groundly"]},
    ],
    ids=["no tools", "null tools", "no servers", "servers not objects"],
)
def test_init_check_fails_closed_on_an_init_it_cannot_read(init):
    # A build that reports its tools differently must not pass the check vacuously.
    assert runs.check_init(init) == (
        "isolation_failed",
        "the run's init event did not list its tools and MCP servers",
    )


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
    assert s.feed(_text("I will now write <script>")) == ([], False)
    assert s.feed(_use("t1", "mcp__groundly__search", {})) == (["Reading the course"], False)
    assert s.feed(_use("t2", "mcp__groundly__search", {})) == (
        [],
        False,
    )  # consecutive repeats collapse
    assert s.feed(_use("t3", "mcp__groundly__submit_questions", {"questions": [1, 2, 3]})) == (
        ["Submitting 3 questions"],
        False,
    )
    assert s.feed(_result("t3", {"accepted": [{}, {}], "rejected": [{}]})) == (
        ["2 stored, 1 rejected"],
        True,
    )
    assert s.feed(_use("t4", "mcp__groundly__review_answers", {"reviews": [1, 2]})) == (
        ["Recording 2 verdicts"],
        False,
    )
    assert s.feed(_result("t4", {"recorded": [7, 8], "awaiting": [], "remaining": 0})) == (
        ["2 verdicts recorded"],
        True,
    )
    assert s.feed(_result("t9", "boom", is_error=True)) == (["A tool call failed"], False)


def test_steps_read_a_result_wrapped_as_result_or_given_as_a_plain_string():
    s = runs.Steps()
    s.feed(_use("t1", "mcp__groundly__submit_questions", {"questions": [1]}))
    wrapped = _result("t1", {"result": {"accepted": [{}], "rejected": []}})
    wrapped["message"]["content"][0]["content"] = wrapped["message"]["content"][0]["content"][0][
        "text"
    ]
    assert s.feed(wrapped) == (["1 stored, 0 rejected"], True)


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


def test_an_event_of_an_unexpected_shape_fails_the_run_instead_of_hanging(fake_claude):
    fake_claude.script([FAKE_INIT, {"type": "assistant", "message": "not an object"}, "SLEEP"])
    runner = runs.Runner()
    _start(runner)
    run = wait_until_ended(runner.view)
    assert run["status"] == "failed" and run["error"]["reason"] == "host_error"
    assert run["error"]["detail"].startswith("unreadable output from claude")


def test_a_deeply_nested_line_is_skipped(fake_claude):
    fake_claude.script([FAKE_INIT, "[" * 100_000, success("Done.")])
    runner = runs.Runner()
    _start(runner)
    run = wait_until_ended(runner.view)
    assert (run["status"], run["summary"]) == ("done", "Done.")


def test_claude_stderr_goes_to_no_file(fake_claude, tmp_path):
    # Debug output is never a Groundly-written file (docs/infrastructure/security.md §5).
    fake_claude.script([FAKE_INIT], exit_code=3, stderr="boom")
    runner = runs.Runner()
    _start(runner)
    run = wait_until_ended(runner.view)
    assert run["error"]["detail"] == "claude exited with code 3: boom"
    assert list((tmp_path / "home" / ".host-run").iterdir()) == []


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


def test_children_claude_leaves_behind_are_ended_when_it_exits(fake_claude):
    fake_claude.record_pids()  # the fake starts a child and exits without waiting for it
    runner = runs.Runner()
    _start(runner)
    claude_pid, child_pid = fake_claude.pids()
    assert wait_until_ended(runner.view)["status"] == "done"
    assert gone(claude_pid) and gone(child_pid)


def test_a_start_is_refused_while_the_last_claude_is_still_exiting(fake_claude):
    fake_claude.script([FAKE_INIT, success(), "SLEEP"])  # reports its result, then lingers
    fake_claude.record_pids()
    runner = runs.Runner()
    _start(runner)
    claude_pid, _ = fake_claude.pids()
    assert wait_until_ended(runner.view)["status"] == "done"
    try:
        with pytest.raises(runs.RunError) as e:
            _start(runner, "grade")
        assert e.value.reason == "run_in_progress"
    finally:
        runner.shutdown()
    assert gone(claude_pid)


def test_build_argv_enables_partial_messages():
    argv = runs.build_argv("/bin/claude", "write", "P", None)
    assert "--include-partial-messages" in argv
    assert argv.index("--include-partial-messages") == argv.index("--verbose") + 1


def test_stream_tracker_tracks_partial_deltas_and_drops_tool_json():
    tracker = runs.StreamTracker()
    assert tracker.on_init() == runs.PhaseEvent("thinking", None)
    assert tracker.feed(stream_start("thinking")) == [
        runs.PhaseEvent("thinking", None),
        runs.DeltaEvent("thinking", "", reset=True),
    ]
    tracker.feed(stream_delta("thinking_delta", "Checking "))
    tracker.feed(stream_delta("thinking_delta", "slides.\nNext line."))
    assert tracker.thought == "Checking slides.\nNext line."
    assert tracker.thought_channel == "thinking"
    # Drops input_json_delta and flips phase immediately on tool_use start
    assert tracker.feed(stream_start("tool_use", name="mcp__groundly__submit_questions")) == [
        runs.PhaseEvent("writing", "Drafting questions")
    ]
    assert tracker.feed(stream_delta("input_json_delta", '{"questions": [')) == []
    assert tracker.thought == "Checking slides.\nNext line."
    # Completed tool_result transitions back to thinking
    assert tracker.feed(tool_result("q1", {"accepted": [{}], "rejected": []})) == [
        runs.PhaseEvent("thinking", None)
    ]
    # review_answers starts as reading, upgrades to writing on completed assistant if reviews present
    assert tracker.feed(stream_start("tool_use", name="mcp__groundly__review_answers")) == [
        runs.PhaseEvent("reading", "Reviewing answers")
    ]
    assert tracker.feed(use("r1", "mcp__groundly__review_answers", {"reviews": [1]})) == [
        runs.PhaseEvent("writing", "Reviewing answers")
    ]
    tracker.reset_settled()
    assert (tracker.phase, tracker.active_tool, tracker.thought, tracker.thought_channel) == (
        None,
        None,
        "",
        None,
    )


def test_stream_tracker_caps_thought_at_max_stream_chars_and_falls_back_on_assistant():
    tracker = runs.StreamTracker()
    tracker.on_init()
    tracker.feed(stream_start("text"))
    tracker.feed(stream_delta("text_delta", "a" * (runs.MAX_STREAM_CHARS + 50)))
    assert len(tracker.thought) == runs.MAX_STREAM_CHARS
    assert tracker.thought == "a" * runs.MAX_STREAM_CHARS
    # Non-partial assistant fallback updates thought when no stream_event ran for the turn
    tracker2 = runs.StreamTracker()
    tracker2.on_init()
    tracker2.feed(
        {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "Plan"}]}}
    )
    assert tracker2.thought == "Plan" and tracker2.thought_channel == "thinking"


def test_steps_refresh_flag_triggers_only_on_nonempty_gate_writes():
    s = runs.Steps()
    assert s.feed(stream_delta("text_delta", "10 stored")) == ([], False)
    assert s.feed(use("q0", "mcp__groundly__submit_questions", {"questions": []})) == (
        ["Submitting 0 questions"],
        False,
    )
    assert s.feed(tool_result("q0", {"accepted": [], "rejected": [{}]})) == (
        ["0 stored, 1 rejected"],
        False,
    )
    assert s.feed(use("q1", "mcp__groundly__submit_questions", {"questions": [1]})) == (
        ["Submitting 1 questions"],
        False,
    )
    assert s.feed(tool_result("q1", {"accepted": [{}], "rejected": []})) == (
        ["1 stored, 0 rejected"],
        True,
    )
    assert s.feed(use("r0", "mcp__groundly__review_answers", {})) == (
        ["Reading answers awaiting review"],
        False,
    )
    assert s.feed(tool_result("r0", {"awaiting": [], "remaining": 0})) == ([], False)
    assert s.feed(use("r1", "mcp__groundly__review_answers", {"reviews": [1]})) == (
        ["Recording 1 verdicts"],
        False,
    )
    assert s.feed(tool_result("r1", {"recorded": [1], "remaining": 0})) == (
        ["1 verdicts recorded"],
        True,
    )


def test_stream_event_before_init_fails_closed(fake_claude):
    fake_claude.script([stream_delta("text_delta", "hello"), FAKE_INIT, success()])
    runner = runs.Runner()
    runner.start("write", "PDSS", 7, "the prompt")
    run = wait_until_ended(runner.view)
    assert run["status"] == "failed" and run["error"]["reason"] == "isolation_failed"
    assert run["phase"] is None and run["thought"] == ""


def test_run_tracks_and_clears_phase_and_thought_on_settlement(fake_claude):
    fake_claude.script(
        [
            FAKE_INIT,
            stream_start("thinking"),
            stream_delta("thinking_delta", "Thinking..."),
            use("t1", "mcp__groundly__search"),
            success("Stored 10."),
        ]
    )
    runner = runs.Runner()
    started = runner.start("write", "PDSS", 7, "the prompt")
    assert (started["phase"], started["active_tool"], started["thought"]) == (
        "starting",
        None,
        "",
    )
    done = wait_until_ended(runner.view)
    assert (
        done["status"],
        done["phase"],
        done["active_tool"],
        done["thought"],
        done["thought_channel"],
    ) == ("done", None, None, "", None)


def _wait_until(view, predicate, timeout=10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = view()
        if run and predicate(run):
            return run
        time.sleep(0.02)
    raise AssertionError(f"condition not met within {timeout}s: {view()}")


def test_stop_and_exit_clear_live_phase_and_thought(fake_claude):
    fake_claude.script(
        [
            FAKE_INIT,
            stream_start("thinking"),
            stream_delta("thinking_delta", "Working..."),
            "SLEEP",
        ]
    )
    runner = runs.Runner()
    runner.start("write", "PDSS", 7, "the prompt")
    try:
        live = _wait_until(
            runner.view,
            lambda r: (
                r["thought"] == "Working..."
                and r["phase"] == "thinking"
                and r["thought_channel"] == "thinking"
            ),
        )
        assert live["active_tool"] is None
        stopped = runner.stop()
    finally:
        runner.shutdown()
    assert stopped is not None
    assert (
        stopped["status"],
        stopped["phase"],
        stopped["active_tool"],
        stopped["thought"],
        stopped["thought_channel"],
    ) == ("stopped", None, None, "", None)

    fake_claude.script(
        [
            FAKE_INIT,
            stream_start("tool_use", name="mcp__groundly__submit_questions"),
            "SLEEP",
        ]
    )
    runner_tool = runs.Runner()
    runner_tool.start("write", "PDSS", 7, "the prompt")
    try:
        live_tool = _wait_until(
            runner_tool.view,
            lambda r: r["phase"] == "writing" and r["active_tool"] == "Drafting questions",
        )
        assert live_tool["status"] == "running"
        stopped_tool = runner_tool.stop()
    finally:
        runner_tool.shutdown()
    assert stopped_tool is not None
    assert (
        stopped_tool["status"],
        stopped_tool["phase"],
        stopped_tool["active_tool"],
        stopped_tool["thought"],
        stopped_tool["thought_channel"],
    ) == ("stopped", None, None, "", None)

    fake_claude.script(
        [FAKE_INIT, stream_start("text"), stream_delta("text_delta", "Partial...")],
        exit_code=2,
        stderr="crashed",
    )
    runner2 = runs.Runner()
    runner2.start("write", "PDSS", 7, "the prompt")
    ended = wait_until_ended(runner2.view)
    assert (
        ended["status"],
        ended["phase"],
        ended["active_tool"],
        ended["thought"],
        ended["thought_channel"],
    ) == ("failed", None, None, "", None)


async def _await_init(runner: runs.Runner, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = runner.view()
        if snap is not None and snap["phase"] == "thinking":
            return snap
        await asyncio.sleep(0.01)
    raise AssertionError(f"run did not pass init within {timeout}s: {runner.view()}")


async def test_runner_subscribe_fans_out_coalesced_deltas_before_phase_and_step(
    fake_claude, tmp_path
):
    gate = tmp_path / "go"
    fake_claude.script(
        [
            FAKE_INIT,
            f"WAIT:{gate}",
            stream_start("thinking"),
            stream_delta("thinking_delta", "One "),
            stream_delta("thinking_delta", "two"),
            use("t1", "mcp__groundly__search"),
            tool_result("t1", {"hits": []}),
            success("Done."),
        ]
    )
    loop = asyncio.get_running_loop()
    runner = runs.Runner()
    runner.start("write", "PDSS", 7, "the prompt")
    try:
        await _await_init(runner)
        snap, q1 = runner.subscribe(loop)
        _, q2 = runner.subscribe(loop)
        assert snap is not None and q1 is not None and q2 is not None
        gate.write_text("1")
        events1 = []
        while (item := await asyncio.wait_for(q1.get(), timeout=5.0)) is not None:
            events1.append(item)
        events2 = []
        while (item := await asyncio.wait_for(q2.get(), timeout=5.0)) is not None:
            events2.append(item)
        assert events1 == events2
        names = [name for name, _ in events1]
        assert "delta" in names and "step" in names and names[-1] == "done"
        assert names.index("delta") < names.index("step")
        deltas = [data for name, data in events1 if name == "delta"]
        assert "".join(d["text"] for d in deltas) == "One two"
        assert events1[-1][1]["status"] == "done" and events1[-1][1]["summary"] == "Done."
    finally:
        runner.shutdown()


async def test_subscriber_queue_overflow_emits_snapshot_resync_without_blocking(
    fake_claude, monkeypatch
):
    # Review Focus #2: small maxsize forces QueueFull -> drains stale frames, emits ("snapshot", ...)
    monkeypatch.setattr(runs, "SUBSCRIBER_QUEUE_MAX", 2)
    fake_claude.script([FAKE_INIT, "SLEEP"])
    loop = asyncio.get_running_loop()
    runner = runs.Runner()
    runner.start("write", "PDSS", 7, "the prompt")
    try:
        await _await_init(runner)
        _, q = runner.subscribe(loop)
        assert q is not None
        for i in range(10):
            runner._publish(
                "delta",
                {"id": 1, "channel": "text", "text": str(i), "reset": False},
                snapshot_fallback={"id": 1, "i": i},
            )
        await asyncio.sleep(0.05)
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        assert any(name == "snapshot" for name, _ in items)
        assert len(items) <= 2
    finally:
        runner.shutdown()


async def test_shutdown_pushes_sentinel_and_wakes_subscribers(fake_claude):
    fake_claude.script([FAKE_INIT, "SLEEP"])
    loop = asyncio.get_running_loop()
    runner = runs.Runner()
    runner.start("write", "PDSS", 7, "the prompt")
    try:
        await _await_init(runner)
        _, q = runner.subscribe(loop)
        assert q is not None
        runner.shutdown()
        items = []
        while (item := await asyncio.wait_for(q.get(), timeout=2.0)) is not None:
            items.append(item)
        assert items[-1][0] == "done" and items[-1][1]["status"] == "stopped"
    finally:
        runner.shutdown()


async def test_delta_coalescer_flushes_after_40ms_while_process_pauses(fake_claude, tmp_path):
    gate = tmp_path / "go"
    fake_claude.script(
        [
            FAKE_INIT,
            f"WAIT:{gate}",
            stream_start("thinking"),
            stream_delta("thinking_delta", "One "),
            stream_delta("thinking_delta", "two"),
            "SLEEP",
        ]
    )
    loop = asyncio.get_running_loop()
    runner = runs.Runner()
    runner.start("write", "PDSS", 7, "the prompt")
    try:
        await _await_init(runner)
        snap, q = runner.subscribe(loop)
        assert snap is not None and q is not None
        gate.write_text("1")
        deltas = []
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            item = await asyncio.wait_for(q.get(), timeout=2.0)
            assert item is not None
            name, data = item
            if name == "delta":
                deltas.append(data)
                break
        assert deltas == [
            {"id": snap["id"], "channel": "thinking", "text": "One two", "reset": True}
        ]
        assert runner.view()["status"] == "running"
    finally:
        runner.shutdown()


async def test_channel_switch_and_reset_flush_prior_buffered_deltas(fake_claude, tmp_path):
    gate = tmp_path / "go"
    fake_claude.script(
        [
            FAKE_INIT,
            f"WAIT:{gate}",
            stream_start("thinking"),
            stream_delta("thinking_delta", "Think"),
            stream_start("text"),
            stream_delta("text_delta", "Say"),
            success("Done."),
        ]
    )
    loop = asyncio.get_running_loop()
    runner = runs.Runner()
    runner.start("write", "PDSS", 7, "the prompt")
    try:
        await _await_init(runner)
        _, q = runner.subscribe(loop)
        assert q is not None
        gate.write_text("1")
        items = []
        while (item := await asyncio.wait_for(q.get(), timeout=5.0)) is not None:
            items.append(item)
        deltas = [data for name, data in items if name == "delta"]
        assert [(d["channel"], d["text"], d["reset"]) for d in deltas] == [
            ("thinking", "Think", True),
            ("text", "Say", True),
        ]
        assert items[-1][0] == "done"
    finally:
        runner.shutdown()


async def test_step_refresh_attaches_only_to_gate_result_step(fake_claude, tmp_path):
    gate = tmp_path / "go"
    mixed_results = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": [{"type": "text", "text": "boom"}],
                    "is_error": True,
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "t2",
                    "content": [
                        {"type": "text", "text": json.dumps({"accepted": [{}], "rejected": []})}
                    ],
                    "is_error": False,
                },
            ]
        },
    }
    fake_claude.script(
        [
            FAKE_INIT,
            f"WAIT:{gate}",
            use("t2", "mcp__groundly__submit_questions", {"questions": [1]}),
            mixed_results,
            success("Stored 1."),
        ]
    )
    loop = asyncio.get_running_loop()
    runner = runs.Runner()
    runner.start("write", "PDSS", 7, "the prompt")
    try:
        await _await_init(runner)
        snap, q = runner.subscribe(loop)
        assert snap is not None and q is not None
        gate.write_text("1")
        items = []
        while (item := await asyncio.wait_for(q.get(), timeout=5.0)) is not None:
            items.append(item)
        step_events = [data for name, data in items if name == "step"]
        assert step_events == [
            {"id": snap["id"], "step": "Submitting 1 questions", "refresh": False},
            {"id": snap["id"], "step": "A tool call failed", "refresh": False},
            {"id": snap["id"], "step": "1 stored, 0 rejected", "refresh": True},
        ]
    finally:
        runner.shutdown()


async def test_subscribe_lifecycle_unsubscribe_and_closed_loop_handling(fake_claude, monkeypatch):
    monkeypatch.setattr(runs, "SUBSCRIBER_QUEUE_MAX", 2)
    fake_claude.script([FAKE_INIT, "SLEEP"])
    loop = asyncio.get_running_loop()
    runner = runs.Runner()
    assert runner.subscribe(loop) == (None, None)

    runner.start("write", "PDSS", 7, "the prompt")
    try:
        await _await_init(runner)
        closed_loop = asyncio.new_event_loop()
        closed_loop.close()
        _, q_closed = runner.subscribe(closed_loop)
        snap, q_live = runner.subscribe(loop)
        _, q_unsub = runner.subscribe(loop)
        assert (
            snap is not None and q_closed is not None and q_live is not None and q_unsub is not None
        )

        runner.unsubscribe(loop, q_unsub)
        runner._publish("delta", {"id": snap["id"], "channel": "text", "text": "a", "reset": False})
        runner._publish("delta", {"id": snap["id"], "channel": "text", "text": "b", "reset": False})
        runner._publish("delta", {"id": snap["id"], "channel": "text", "text": "c", "reset": False})
        # Trigger stop while q_live is already at capacity (2 items pending) -> done + sentinel land
        runner.stop()
        assert q_unsub.empty()
        items = []
        while (item := await asyncio.wait_for(q_live.get(), timeout=2.0)) is not None:
            items.append(item)
        assert items[-1][0] == "done" and items[-1][1]["status"] == "stopped"
        settled, q_after = runner.subscribe(loop)
        assert settled is not None and settled["status"] == "stopped" and q_after is None
    finally:
        runner.shutdown()


async def test_stop_race_after_result_emits_single_done_and_sentinel(fake_claude, tmp_path):
    gate = tmp_path / "go"
    fake_claude.script([FAKE_INIT, f"WAIT:{gate}", success("Stored."), "SLEEP"])
    loop = asyncio.get_running_loop()
    runner = runs.Runner()
    runner.start("write", "PDSS", 7, "the prompt")
    try:
        await _await_init(runner)
        _, q = runner.subscribe(loop)
        assert q is not None
        gate.write_text("1")
        await asyncio.to_thread(wait_until_ended, runner.view)
        runner.stop()
        runner.shutdown()
        items = []
        while (item := await asyncio.wait_for(q.get(), timeout=2.0)) is not None:
            items.append(item)
        done_events = [data for name, data in items if name == "done"]
        assert len(done_events) == 1
        assert done_events[0]["status"] == "done"
        assert done_events[0]["summary"] == "Stored."
    finally:
        runner.shutdown()


async def test_subscribe_mid_coalesce_flushes_pending_delta_without_duplication(
    fake_claude, tmp_path, monkeypatch
):
    gate1 = tmp_path / "gate1"
    gate2 = tmp_path / "gate2"
    gate3 = tmp_path / "gate3"
    fake_claude.script(
        [
            FAKE_INIT,
            f"WAIT:{gate1}",
            stream_start("thinking"),
            stream_delta("thinking_delta", "One "),
            f"WAIT:{gate2}",
            stream_delta("thinking_delta", "two"),
            f"WAIT:{gate3}",
            stream_delta("thinking_delta", "three"),
            success("Done."),
        ]
    )
    loop = asyncio.get_running_loop()
    runner = runs.Runner()
    runner.start("write", "PDSS", 7, "the prompt")
    try:
        await _await_init(runner)
        _, q1 = runner.subscribe(loop)
        assert q1 is not None
        gate1.write_text("1")

        first = await asyncio.wait_for(q1.get(), timeout=2.0)
        assert first is not None and first[0] == "delta" and first[1]["text"] == "One "

        # Hold the next reset=False delta ("two") buffered in _pending_delta while q2 subscribes
        monkeypatch.setattr(runs, "DELTA_COALESCE_S", 5.0)
        gate2.write_text("1")
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            view = runner.view()
            if view is not None and view["thought"] == "One two":
                break
            await asyncio.sleep(0.01)
        assert runner.view()["thought"] == "One two"

        snap2, q2 = runner.subscribe(loop)
        assert snap2 is not None and q2 is not None
        assert snap2["thought"] == "One two"

        monkeypatch.setattr(runs, "DELTA_COALESCE_S", 0.04)
        gate3.write_text("1")

        events1 = [first]
        while (item := await asyncio.wait_for(q1.get(), timeout=5.0)) is not None:
            events1.append(item)
        events2 = []
        while (item := await asyncio.wait_for(q2.get(), timeout=5.0)) is not None:
            events2.append(item)

        deltas1 = [data for name, data in events1 if name == "delta"]
        deltas2 = [data for name, data in events2 if name == "delta"]
        assert [d["text"] for d in deltas1] == ["One ", "two", "three"]
        assert [d["text"] for d in deltas2] == ["three"]
        assert deltas2[0]["reset"] is False
    finally:
        runner.shutdown()


async def test_coalesced_deltas_cap_at_max_stream_chars_with_reset(
    fake_claude, tmp_path, monkeypatch
):
    gate = tmp_path / "go"
    fake_claude.script(
        [
            FAKE_INIT,
            f"WAIT:{gate}",
            stream_start("thinking"),
            stream_delta("thinking_delta", "prefix"),
            f"WAIT:{tmp_path / 'go2'}",
            stream_delta("thinking_delta", "x" * 1500),
            stream_delta("thinking_delta", "y" * 1000),
            success("Done."),
        ]
    )
    loop = asyncio.get_running_loop()
    runner = runs.Runner()
    runner.start("write", "PDSS", 7, "the prompt")
    try:
        await _await_init(runner)
        snap, q = runner.subscribe(loop)
        assert snap is not None and q is not None
        gate.write_text("1")

        first = await asyncio.wait_for(q.get(), timeout=2.0)
        while first is not None and first[0] != "delta":
            first = await asyncio.wait_for(q.get(), timeout=2.0)
        assert first is not None and first[1]["text"] == "prefix"

        monkeypatch.setattr(runs, "DELTA_COALESCE_S", 5.0)
        (tmp_path / "go2").write_text("1")
        items = []
        while (item := await asyncio.wait_for(q.get(), timeout=5.0)) is not None:
            items.append(item)
        deltas = [data for name, data in items if name == "delta"]
        assert len(deltas) == 1
        assert deltas[0]["reset"] is True
        assert len(deltas[0]["text"]) == runs.MAX_STREAM_CHARS
        assert deltas[0]["text"] == ("x" * 1000) + ("y" * 1000)
        assert items[-1][0] == "done" and items[-1][1]["status"] == "done"
    finally:
        runner.shutdown()


def test_steps_preserve_repeated_gate_writes_with_refresh():
    s = runs.Steps()
    assert s.feed_items(use("s1", "mcp__groundly__search")) == [("Reading the course", False)]
    assert s.feed_items(use("s2", "mcp__groundly__search")) == []
    s.feed_items(use("q1", "mcp__groundly__submit_questions", {"questions": [1]}))
    s.feed_items(use("q2", "mcp__groundly__submit_questions", {"questions": [1]}))
    assert s.feed_items(tool_result("q1", {"accepted": [{}], "rejected": []})) == [
        ("1 stored, 0 rejected", True)
    ]
    assert s.feed_items(tool_result("q2", {"accepted": [{}], "rejected": []})) == [
        ("1 stored, 0 rejected", True)
    ]


async def test_crashing_run_flushes_buffered_delta_before_done(fake_claude, tmp_path, monkeypatch):
    gate = tmp_path / "go"
    monkeypatch.setattr(runs, "DELTA_COALESCE_S", 5.0)
    fake_claude.script(
        [
            FAKE_INIT,
            f"WAIT:{gate}",
            stream_start("text"),
            stream_delta("text_delta", "Before crash"),
        ],
        exit_code=2,
        stderr="boom",
    )
    loop = asyncio.get_running_loop()
    runner = runs.Runner()
    runner.start("write", "PDSS", 7, "the prompt")
    try:
        await _await_init(runner)
        snap, q = runner.subscribe(loop)
        assert snap is not None and q is not None
        gate.write_text("1")
        items = []
        while (item := await asyncio.wait_for(q.get(), timeout=5.0)) is not None:
            items.append(item)
        deltas = [data for name, data in items if name == "delta"]
        assert deltas == [
            {"id": snap["id"], "channel": "text", "text": "Before crash", "reset": True}
        ]
        assert items[-1][0] == "done"
        assert items[-1][1]["status"] == "failed"
        assert items[-1][1]["error"] == {
            "reason": "host_exit",
            "detail": "claude exited with code 2: boom",
        }
    finally:
        runner.shutdown()


async def test_non_partial_assistant_fallback_streams_delta_and_phase_to_subscribers(
    fake_claude, tmp_path
):
    gate = tmp_path / "go"
    fake_claude.script(
        [
            FAKE_INIT,
            f"WAIT:{gate}",
            {
                "type": "assistant",
                "message": {"content": [{"type": "thinking", "thinking": "Step one"}]},
            },
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Final note"}]},
            },
            success("Done."),
        ]
    )
    loop = asyncio.get_running_loop()
    runner = runs.Runner()
    runner.start("write", "PDSS", 7, "the prompt")
    try:
        await _await_init(runner)
        snap, q = runner.subscribe(loop)
        assert snap is not None and q is not None
        gate.write_text("1")
        items = []
        while (item := await asyncio.wait_for(q.get(), timeout=5.0)) is not None:
            items.append(item)
        deltas = [data for name, data in items if name == "delta"]
        assert [(d["channel"], d["text"], d["reset"]) for d in deltas] == [
            ("thinking", "Step one", True),
            ("text", "Final note", True),
        ]
        assert items[-1][0] == "done" and items[-1][1]["status"] == "done"
    finally:
        runner.shutdown()
