"""CLI: `groundly serve` — loopback topic-tree page, opened in the browser."""

import socket
from pathlib import Path

from typer.testing import CliRunner

from groundly.cli import app

runner = CliRunner()
PAGE = Path(__file__).parents[2] / "groundly" / "web" / "page.html"


def _patch(monkeypatch):
    import uvicorn

    runs: list[dict] = []
    opened: list[str] = []
    monkeypatch.setattr(uvicorn, "run", lambda app_, **kw: runs.append(kw))
    monkeypatch.setattr("groundly.cli.serve._open_browser", opened.append)
    return runs, opened


def test_serve_binds_loopback_and_opens_browser(monkeypatch):
    runs, opened = _patch(monkeypatch)
    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0, result.output
    assert [(r["host"], r["port"]) for r in runs] == [("127.0.0.1", 8765)]
    assert opened == ["http://127.0.0.1:8765/"]


def test_serve_port_flag(monkeypatch):
    runs, opened = _patch(monkeypatch)
    result = runner.invoke(app, ["serve", "--port", "9123"])
    assert result.exit_code == 0, result.output
    assert runs[0]["port"] == 9123
    assert opened == ["http://127.0.0.1:9123/"]


def test_serve_names_a_busy_port_and_the_flag(monkeypatch):
    runs, opened = _patch(monkeypatch)
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = held.getsockname()[1]
        result = runner.invoke(app, ["serve", "--port", str(port)])
    assert result.exit_code == 1
    assert str(port) in result.output and "--port" in result.output
    assert "Traceback" not in result.output
    assert runs == [] and opened == []


def test_page_inserts_content_only_as_text():
    page = PAGE.read_text(encoding="utf-8")
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert sink not in page, sink


def test_page_loads_nothing_external():
    page = PAGE.read_text(encoding="utf-8")
    assert "http://" not in page and "https://" not in page
    assert " onclick=" not in page
