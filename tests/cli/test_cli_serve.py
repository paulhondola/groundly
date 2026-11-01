"""CLI: `groundly serve` — loopback topic-tree page, opened in the browser."""

import errno
import socket
from pathlib import Path

from typer.testing import CliRunner

from groundly.cli import app

runner = CliRunner()
PAGE = Path(__file__).parents[2] / "groundly" / "web" / "page.html"


def _patch(monkeypatch, probe_free=True):
    import uvicorn

    runs: list[dict] = []
    opened: list[str] = []
    monkeypatch.setattr(uvicorn, "run", lambda app_, **kw: runs.append(kw))
    monkeypatch.setattr("groundly.cli.serve._open_browser", opened.append)
    if probe_free:  # the default port may be held by a real `groundly serve` on this machine
        monkeypatch.setattr(socket.socket, "bind", lambda self, address: None)
    return runs, opened


def test_serve_binds_loopback_and_opens_browser(monkeypatch):
    runs, opened = _patch(monkeypatch)
    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0, result.output
    assert [(r["host"], r["port"]) for r in runs] == [("127.0.0.1", 8765)]
    assert opened == ["http://127.0.0.1:8765/"]
    assert "http://127.0.0.1:8765/" in result.output


def test_serve_port_flag(monkeypatch):
    runs, opened = _patch(monkeypatch)
    result = runner.invoke(app, ["serve", "--port", "9123"])
    assert result.exit_code == 0, result.output
    assert runs[0]["port"] == 9123
    assert opened == ["http://127.0.0.1:9123/"]


def test_serve_names_a_busy_port_and_the_flag(monkeypatch):
    runs, opened = _patch(monkeypatch, probe_free=False)
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = held.getsockname()[1]
        result = runner.invoke(app, ["serve", "--port", str(port)])
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
    page = PAGE.read_text(encoding="utf-8")
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert sink not in page, sink


def test_page_loads_nothing_external():
    page = PAGE.read_text(encoding="utf-8")
    assert "http://" not in page and "https://" not in page
    assert " onclick=" not in page


def test_page_links_a_source_only_when_the_server_says_it_is_a_stored_pdf():
    page = PAGE.read_text(encoding="utf-8")
    assert "src.pdf === true" in page
    assert '.endsWith(".pdf")' not in page
