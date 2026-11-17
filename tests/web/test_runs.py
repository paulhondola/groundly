"""A page button starts the student's own Claude Code headless. These tests pin what that run
may touch (only Groundly's tools, none of the student's settings) and what the page learns
from it (steps from tool names, never model text). No test calls a model."""

import json
import subprocess
import sys
from pathlib import Path

from groundly.web import runs


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
