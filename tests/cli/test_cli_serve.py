"""CLI: `groundly serve` — loopback topic-tree page, opened in the browser."""

import errno
import re
import socket
from pathlib import Path

import pytest
from typer.testing import CliRunner

from groundly.cli import app

runner = CliRunner()
ROOT = Path(__file__).parents[2]
WEB = ROOT / "groundly" / "web"
WEB_SRC = ROOT / "web" / "src"

AUTHORED_SOURCE = (WEB / "page.html").read_text(encoding="utf-8") + "".join(
    p.read_text(encoding="utf-8")
    for p in sorted(WEB_SRC.rglob("*"))
    if p.is_file() and "__tests__" not in p.parts and p.suffix in {".ts", ".svelte", ".css"}
)
SHIPPED_BUNDLE = "".join(
    (WEB / f).read_text(encoding="utf-8")
    for f in ("page.html", "static/page.js", "static/page.css")
)

_ALLOWED_BUNDLE_URLS = (
    "http://www.w3.org/2000/svg",
    "http://www.w3.org/1999/xhtml",
    "http://www.w3.org/1998/Math/MathML",
    "http://www.w3.org/1999/xlink",
)


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("GROUNDLY_HOME", str(home_dir))
    return home_dir


@pytest.fixture(autouse=True)
def _activity_registration_stays_in_the_test(monkeypatch):
    from groundly.mcp.server import mcp

    monkeypatch.setattr(mcp, "middleware", list(mcp.middleware))


def _patch(monkeypatch, probe_free=True):
    import uvicorn

    runs: list[dict] = []
    opened: list[str] = []

    def fake_run(self, sockets=None):
        runs.append(
            {
                "app": self.config.app,
                "host": self.config.host,
                "port": self.config.port,
                "sockets": sockets,
                "proxy_headers": self.config.proxy_headers,
                "timeout_graceful_shutdown": self.config.timeout_graceful_shutdown,
                "server": self,
            }
        )

    monkeypatch.setattr(uvicorn.Server, "run", fake_run)
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
    assert runs[0]["timeout_graceful_shutdown"]  # an open /mcp stream cannot hold shutdown
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
    connect = "claude mcp add --scope user --transport http groundly http://127.0.0.1:8765/mcp"
    assert connect in result.output.splitlines()[-1]
    assert "http://127.0.0.1:8765/" in result.output  # fallback when *.localhost won't resolve


def _seen_while_serving(monkeypatch) -> list:
    """Patch the server so each run records what start.json says while it serves."""
    import uvicorn

    from groundly.core import startfile

    seen: list = []
    _patch(monkeypatch)
    monkeypatch.setattr(
        uvicorn.Server, "run", lambda self, sockets=None: seen.append(startfile.read())
    )
    monkeypatch.setattr("atexit.register", lambda *a, **kw: None)
    return seen


def test_start_announces_itself_while_serving_and_cleans_up(monkeypatch):
    import os
    from importlib.metadata import version

    from groundly.core import startfile
    from groundly.core.startfile import StartInfo

    seen = _seen_while_serving(monkeypatch)
    result = runner.invoke(app, ["start", "--port", "9123"])
    assert result.exit_code == 0, result.output
    assert seen == [StartInfo(os.getpid(), 9123, version("groundly"))]
    assert not startfile.path().exists()


def test_serve_writes_no_runfile(monkeypatch):
    from groundly.core import startfile

    seen = _seen_while_serving(monkeypatch)
    assert runner.invoke(app, ["serve"]).exit_code == 0
    assert seen == [None]
    assert not startfile.path().exists()


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


@pytest.mark.parametrize("verb", ["serve", "start"])
def test_tailscale_flag_binds_both_sockets_attaches_remote_and_prints_qr(monkeypatch, verb):
    from groundly.cli.app import console
    from groundly.web.remote import RemoteRuntime, render_terminal_qr
    from groundly.web.tailscale import TailscaleInfo

    runs, opened = _patch(monkeypatch)
    bound: list[tuple[str, int]] = []
    monkeypatch.setattr(socket.socket, "bind", lambda self, address: bound.append(address))
    ts_info = TailscaleInfo(ipv4="100.101.102.103", dns_name="mybox.tailnet.ts.net")
    monkeypatch.setattr("groundly.web.tailscale.discover_tailscale", lambda: ts_info)

    result = runner.invoke(app, [verb, "--tailscale"])
    assert result.exit_code == 0, result.output
    assert bound == [("127.0.0.1", 8765), ("100.101.102.103", 8765)]
    assert len(runs) == 1
    assert runs[0]["host"] == "127.0.0.1" and runs[0]["port"] == 8765
    assert len(runs[0]["sockets"]) == 2
    assert all(sock.fileno() == -1 for sock in runs[0]["sockets"])
    assert opened == ["http://groundly.localhost:8765/"]

    remote = runs[0]["app"].user_middleware[0].kwargs["remote"]
    assert isinstance(remote, RemoteRuntime)
    assert remote.ts_info == ts_info and remote.port == 8765
    assert f"Tailscale: http://{ts_info.preferred_host}:8765/" in result.output
    assert "Pair URL:" in result.output and "/pair#" in result.output
    assert render_terminal_qr(remote.pairing_url).rstrip("\n") in result.output
    if verb == "start":
        assert runs[0]["app"].state.runner.mcp_url == "http://127.0.0.1:8765/mcp"
        assert "http://127.0.0.1:8765/mcp" in result.output

    assert remote.on_paired is not None
    with console.capture() as capture:
        remote.on_paired(
            "[red]iPhone[/red]", "100.64.0.9", "http://mybox.tailnet.ts.net:8765/pair#next"
        )
    assert (
        "Paired [red]iPhone[/red] (100.64.0.9) — next pair link: "
        "http://mybox.tailnet.ts.net:8765/pair#next"
    ) in capture.get()


@pytest.mark.parametrize("verb", ["serve", "start"])
def test_explicit_tailscale_fails_when_unavailable(monkeypatch, verb):
    runs, opened = _patch(monkeypatch)
    monkeypatch.setattr("groundly.web.tailscale.discover_tailscale", lambda: None)

    result = runner.invoke(app, [verb, "--tailscale"])
    assert result.exit_code == 1
    assert "--no-tailscale" in result.output
    assert runs == [] and opened == []


def test_config_tailscale_warns_and_falls_back_to_loopback_when_unavailable(monkeypatch, home):
    (home / "config.toml").write_text("[web]\ntailscale = true\n", encoding="utf-8")
    runs, opened = _patch(monkeypatch)
    bound: list[tuple[str, int]] = []
    monkeypatch.setattr(socket.socket, "bind", lambda self, address: bound.append(address))
    monkeypatch.setattr("groundly.web.tailscale.discover_tailscale", lambda: None)

    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0, result.output
    assert "127.0.0.1 only" in result.output
    assert bound == [("127.0.0.1", 8765)]
    assert len(runs[0]["sockets"]) == 1
    assert runs[0]["app"].user_middleware[0].kwargs["remote"] is None
    assert opened == ["http://groundly.localhost:8765/"]


def test_no_tailscale_flag_overrides_config_without_discovery(monkeypatch, home):
    (home / "config.toml").write_text("[web]\ntailscale = true\n", encoding="utf-8")
    runs, opened = _patch(monkeypatch)
    bound: list[tuple[str, int]] = []
    monkeypatch.setattr(socket.socket, "bind", lambda self, address: bound.append(address))

    def fail_if_called():
        raise AssertionError("discover_tailscale should not be called")

    monkeypatch.setattr("groundly.web.tailscale.discover_tailscale", fail_if_called)

    result = runner.invoke(app, ["serve", "--no-tailscale"])
    assert result.exit_code == 0, result.output
    assert "127.0.0.1 only" not in result.output
    assert bound == [("127.0.0.1", 8765)]
    assert len(runs[0]["sockets"]) == 1
    assert runs[0]["app"].user_middleware[0].kwargs["remote"] is None
    assert opened == ["http://groundly.localhost:8765/"]


def test_serve_ignores_user_home_tailscale_config_via_isolated_groundly_home(monkeypatch, tmp_path):
    user_home = tmp_path / "user_home"
    (user_home / ".groundly").mkdir(parents=True)
    (user_home / ".groundly" / "config.toml").write_text(
        "[web]\ntailscale = true\n", encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(user_home))
    runs, opened = _patch(monkeypatch)

    def fail_if_called():
        raise AssertionError("discover_tailscale should not be called")

    monkeypatch.setattr("groundly.web.tailscale.discover_tailscale", fail_if_called)

    result = runner.invoke(app, ["start"])
    assert result.exit_code == 0, result.output
    assert [(r["host"], r["port"]) for r in runs] == [("127.0.0.1", 8765)]
    assert runs[0]["app"].user_middleware[0].kwargs["remote"] is None
    assert opened == ["http://groundly.localhost:8765/"]


def test_second_socket_bind_failure_closes_first_socket_and_exits_cleanly(monkeypatch):
    from groundly.web.tailscale import TailscaleInfo

    runs, opened = _patch(monkeypatch)
    ts_info = TailscaleInfo(ipv4="100.101.102.103")
    monkeypatch.setattr("groundly.web.tailscale.discover_tailscale", lambda: ts_info)
    bound_sockets: list[socket.socket] = []

    def bind_fail_second(self, address):
        if address[0] == "127.0.0.1":
            bound_sockets.append(self)
            return
        raise OSError(errno.EADDRNOTAVAIL, "Can't assign requested address")

    monkeypatch.setattr(socket.socket, "bind", bind_fail_second)

    result = runner.invoke(app, ["serve", "--tailscale"])
    assert result.exit_code == 1
    assert "Can't assign requested address" in result.output
    assert "Traceback" not in result.output
    assert len(bound_sockets) == 1 and bound_sockets[0].fileno() == -1
    assert runs == [] and opened == []


def test_page_inserts_content_only_as_text():
    for sink in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "{@html",
    ):
        assert sink not in AUTHORED_SOURCE, sink


def test_page_loads_nothing_external():
    assert "http://" not in AUTHORED_SOURCE and "https://" not in AUTHORED_SOURCE
    assert ' onclick="' not in AUTHORED_SOURCE
    assert " onclick=" not in (WEB / "page.html").read_text(encoding="utf-8")


def test_page_links_a_source_only_when_the_server_says_it_is_a_stored_pdf():
    assert "src.pdf === true" in AUTHORED_SOURCE
    assert '.endsWith(".pdf")' not in AUTHORED_SOURCE


def test_the_page_tells_the_student_how_to_get_questions_and_a_review():
    assert "Write questions on" in AUTHORED_SOURCE and "grade my test" in AUTHORED_SOURCE


def test_the_page_never_calls_a_key_verified():
    assert "verified" not in AUTHORED_SOURCE.lower()


def test_the_page_keeps_a_test_in_session_storage_and_sends_a_random_token():
    assert "sessionStorage" in AUTHORED_SOURCE and "crypto.randomUUID()" in AUTHORED_SOURCE


def test_serve_ignores_forwarded_headers(monkeypatch):
    """The write route compares Origin with the request's own scheme and host; a forwarded
    header must not be able to change either."""
    runs, _ = _patch(monkeypatch)
    assert runner.invoke(app, ["serve"]).exit_code == 0
    assert runs[0]["proxy_headers"] is False


def test_the_page_drops_a_test_whose_question_was_removed():
    assert '"unknown_question"' in AUTHORED_SOURCE


def test_the_suggested_host_sentence_quotes_the_topic_title():
    """A title may come from an imported bundle: it reaches the host quoted, as data."""
    assert '"Write questions on \\"" + title + "\\" for "' in AUTHORED_SOURCE


def test_a_test_dropped_for_a_removed_question_is_never_saved_again():
    assert "createTestPersister" in AUTHORED_SOURCE
    assert "markDead()" in AUTHORED_SOURCE
    assert "if (!dead)" in AUTHORED_SOURCE


def test_page_starts_runs_through_the_api_only():
    assert (
        "/api/runs" in AUTHORED_SOURCE
        and '"/api/subjects/{subject}/runs"' in AUTHORED_SOURCE
        and "/api/runs/stream" in AUTHORED_SOURCE
        and "/api/runs/stop" in AUTHORED_SOURCE
    )
    assert "Write questions for its subtopics" in AUTHORED_SOURCE and "Grade now" in AUTHORED_SOURCE


def test_page_keeps_the_phrases_as_the_fallback():
    assert "Say to your agent" in AUTHORED_SOURCE and "grade my test" in AUTHORED_SOURCE


def test_shipped_bundle_is_built_clean_and_self_contained():
    page_js = (WEB / "static" / "page.js").read_text(encoding="utf-8")
    page_css = (WEB / "static" / "page.css").read_text(encoding="utf-8")
    assert len(page_js) > 0 and len(page_css) > 0
    assert "verified" not in SHIPPED_BUNDLE.lower()

    cleaned = SHIPPED_BUNDLE
    for ns in _ALLOWED_BUNDLE_URLS:
        cleaned = cleaned.replace(ns, "")
    cleaned = re.sub(r"https://svelte\.dev/e/[a-z0-9_]+", "", cleaned)
    cleaned = re.sub(r"/\*! tailwindcss [^*]*https://tailwindcss\.com \*/", "", cleaned)
    assert "http://" not in cleaned and "https://" not in cleaned

    for marker in (
        "/api/runs/stream",
        "/api/runs/stop",
        "unknown_question",
        "sessionStorage",
        "randomUUID",
        "Write questions for its subtopics",
        "Grade now",
        "grade my test",
    ):
        assert marker in SHIPPED_BUNDLE, marker


def test_handle_exit_shuts_down_runner_once_before_graceful_wait(monkeypatch):
    import signal
    import time

    runs, _ = _patch(monkeypatch)
    assert runner.invoke(app, ["serve"]).exit_code == 0
    server = runs[0]["server"]
    calls: list[str] = []
    monkeypatch.setattr(runs[0]["app"].state.runner, "shutdown", lambda: calls.append("shutdown"))

    server.handle_exit(signal.SIGTERM, None)
    deadline = time.monotonic() + 2.0
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    server.handle_exit(signal.SIGTERM, None)
    assert calls == ["shutdown"]
    assert server.should_exit is True


def test_start_logs_its_mcp_sessions_as_http(monkeypatch):
    from groundly.mcp.activity import ActivityMiddleware
    from groundly.mcp.server import mcp

    _patch(monkeypatch)
    assert runner.invoke(app, ["start"]).exit_code == 0
    assert [m.source for m in mcp.middleware if isinstance(m, ActivityMiddleware)] == ["mcp_http"]


def test_serve_logs_no_mcp_activity(monkeypatch):
    from groundly.mcp.activity import ActivityMiddleware
    from groundly.mcp.server import mcp

    _patch(monkeypatch)
    assert runner.invoke(app, ["serve"]).exit_code == 0
    assert not [m for m in mcp.middleware if isinstance(m, ActivityMiddleware)]
