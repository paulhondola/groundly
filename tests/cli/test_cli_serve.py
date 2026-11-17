"""CLI: `groundly serve` — loopback topic-tree page, opened in the browser."""

import errno
import socket
from pathlib import Path

import pytest
from typer.testing import CliRunner

from groundly.cli import app

runner = CliRunner()
WEB = Path(__file__).parents[2] / "groundly" / "web"
# The shell and its script and stylesheet: the guarantees below hold across all three.
PAGE = "".join(
    (WEB / f).read_text(encoding="utf-8")
    for f in ("page.html", "static/page.js", "static/page.css")
)


def _patch(monkeypatch, probe_free=True):
    import uvicorn

    runs: list[dict] = []
    opened: list[str] = []
    monkeypatch.setattr(uvicorn, "run", lambda app_, **kw: runs.append({**kw, "app": app_}))
    monkeypatch.setattr("groundly.cli.serve._open_browser", opened.append)
    if probe_free:  # the default port may be held by a real `groundly serve` on this machine
        monkeypatch.setattr(socket.socket, "bind", lambda self, address: None)
    return runs, opened


def test_serve_binds_loopback_and_opens_browser(monkeypatch):
    runs, opened = _patch(monkeypatch)
    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0, result.output
    assert [(r["host"], r["port"]) for r in runs] == [("127.0.0.1", 8765)]
    assert runs[0]["proxy_headers"] is False
    assert opened == ["http://groundly.localhost:8765/"]
    assert "http://127.0.0.1:8765/" in result.output  # fallback when *.localhost won't resolve
    assert runs[0]["app"].state.runner.mcp_url is None  # a page run starts its own Groundly


def test_a_terminal_hangup_shuts_the_page_down_like_ctrl_c(monkeypatch):
    # uvicorn shuts down (and the page kills a running `claude`) on SIGINT/SIGTERM only;
    # closing the terminal sends SIGHUP.
    import signal

    _patch(monkeypatch)
    raised = []
    monkeypatch.setattr(signal, "raise_signal", raised.append)
    previous = signal.getsignal(signal.SIGHUP)
    try:
        assert runner.invoke(app, ["serve"]).exit_code == 0
        handler = signal.getsignal(signal.SIGHUP)
        assert callable(handler)
        handler(signal.SIGHUP, None)
    finally:
        signal.signal(signal.SIGHUP, previous)
    assert raised == [signal.SIGTERM]


def test_start_binds_loopback_opens_page_and_prints_both_urls(monkeypatch):
    from groundly.cli.app import console

    runs, opened = _patch(monkeypatch)
    monkeypatch.setattr(console, "width", 60)  # narrower than the connect line
    result = runner.invoke(app, ["start"])
    assert result.exit_code == 0, result.output
    assert [(r["host"], r["port"]) for r in runs] == [("127.0.0.1", 8765)]
    assert runs[0]["proxy_headers"] is False
    assert opened == ["http://groundly.localhost:8765/"]
    # MCP clients get the IP: they use the OS resolver, which may send *.localhost upstream
    assert "groundly.localhost:8765/mcp" not in result.output
    # one unwrapped line, so it pastes as a command at any terminal width
    # a page run shares this process's model load through its own /mcp
    assert runs[0]["app"].state.runner.mcp_url == "http://127.0.0.1:8765/mcp"
    connect = "claude mcp add --transport http groundly http://127.0.0.1:8765/mcp"
    assert connect in result.output.splitlines()[-1]
    assert "http://127.0.0.1:8765/" in result.output  # fallback when *.localhost won't resolve


def test_serve_port_flag(monkeypatch):
    runs, opened = _patch(monkeypatch)
    result = runner.invoke(app, ["serve", "--port", "9123"])
    assert result.exit_code == 0, result.output
    assert runs[0]["port"] == 9123
    assert opened == ["http://groundly.localhost:9123/"]


@pytest.mark.parametrize("verb", ["serve", "start"])
def test_a_busy_port_is_named_with_the_flag(monkeypatch, verb):
    runs, opened = _patch(monkeypatch, probe_free=False)
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = held.getsockname()[1]
        result = runner.invoke(app, [verb, "--port", str(port)])
    assert result.exit_code == 1
    assert str(port) in result.output and "--port" in result.output
    assert "Traceback" not in result.output
    assert runs == [] and opened == []


def test_serve_refuses_ports_outside_the_valid_range(monkeypatch):
    runs, opened = _patch(monkeypatch)
    for bad in ("0", "70000"):
        result = runner.invoke(app, ["serve", "--port", bad])
        assert result.exit_code == 2
        assert "--port" in result.output
    assert runs == [] and opened == []


def test_serve_names_the_real_cause_when_the_bind_is_refused(monkeypatch):
    runs, opened = _patch(monkeypatch)

    def refuse(self, address):
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(socket.socket, "bind", refuse)
    result = runner.invoke(app, ["serve", "--port", "80"])
    assert result.exit_code == 1
    assert "cannot bind port 80: Permission denied" in result.output
    assert "already in use" not in result.output
    assert runs == [] and opened == []


def test_page_inserts_content_only_as_text():
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert sink not in PAGE, sink


def test_page_loads_nothing_external():
    assert "http://" not in PAGE and "https://" not in PAGE
    assert " onclick=" not in PAGE


def test_page_links_a_source_only_when_the_server_says_it_is_a_stored_pdf():
    assert "src.pdf === true" in PAGE
    assert '.endsWith(".pdf")' not in PAGE


def test_the_page_tells_the_student_how_to_get_questions_and_a_review():
    assert "Write questions on" in PAGE and "grade my test" in PAGE


def test_the_page_never_calls_a_key_verified():
    assert "verified" not in PAGE.lower()


def test_the_page_keeps_a_test_in_session_storage_and_sends_a_random_token():
    assert "sessionStorage" in PAGE and "crypto.randomUUID()" in PAGE


def test_serve_ignores_forwarded_headers(monkeypatch):
    """The write route compares Origin with the request's own scheme and host; a forwarded
    header must not be able to change either."""
    runs, _ = _patch(monkeypatch)
    assert runner.invoke(app, ["serve"]).exit_code == 0
    assert runs[0]["proxy_headers"] is False


def test_the_page_drops_a_test_whose_question_was_removed():
    assert '"unknown_question"' in PAGE


def test_the_suggested_host_sentence_quotes_the_topic_title():
    """A title may come from an imported bundle: it reaches the host quoted, as data."""
    assert '"Write questions on \\"" + title + "\\" for "' in PAGE


def test_a_test_dropped_for_a_removed_question_is_never_saved_again():
    assert "function store() { if (!dead) saveTest(key, test); }" in PAGE


def test_page_starts_runs_through_the_api_only():
    assert "/api/runs" in PAGE and '"/runs"' in PAGE and "/api/runs/stop" in PAGE
    assert "Write questions for its subtopics" in PAGE and "Grade now" in PAGE


def test_page_keeps_the_phrases_as_the_fallback():
    assert "Say to your agent" in PAGE and "grade my test" in PAGE
