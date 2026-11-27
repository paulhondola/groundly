"""stdio forwarding: the three model tools go to a running `start`, everything else stays local,
and nothing is retried locally once a call may have reached `start`."""

import json
import os
import subprocess
import threading
import time

import httpx2
import pytest
import uvicorn
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request

from groundly.core import startfile
from groundly.core.startfile import VERSION
from groundly.mcp.forward import FORWARD_HEADER, STOPPED_MESSAGE, ForwardMiddleware


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path))


def _fake_start(version: str = VERSION, seen: list | None = None) -> FastMCP:
    server = FastMCP("groundly", version=version)

    @server.tool
    def search(subject: str, query: str, k: int | None = None) -> list[dict]:
        if seen is not None:
            seen.append(get_http_request().headers.get(FORWARD_HEADER))
        return [{"from": "start"}]

    @server.tool
    def submit_tree(subject: str, tree: list[dict]) -> dict:
        return {"accepted": True, "from": "start"}

    @server.tool
    def submit_questions(subject: str, topic_id: int, questions: list[dict]) -> dict:
        raise ToolError("unknown subject 'x'")

    @server.tool
    def outline(subject: str) -> dict:
        return {"from": "start"}

    return server


@pytest.fixture
def local():
    """The stdio side: stub tools answering "local", counting calls, behind ForwardMiddleware."""
    server = FastMCP("groundly")
    calls: list[str] = []

    @server.tool
    def search(subject: str, query: str, k: int | None = None) -> list[dict]:
        calls.append("search")
        return [{"from": "local"}]

    @server.tool
    def submit_tree(subject: str, tree: list[dict]) -> dict:
        calls.append("submit_tree")
        return {"accepted": True, "from": "local"}

    @server.tool
    def submit_questions(subject: str, topic_id: int, questions: list[dict]) -> dict:
        calls.append("submit_questions")
        return {"accepted": [], "rejected": [], "from": "local"}

    @server.tool
    def outline(subject: str) -> dict:
        calls.append("outline")
        return {"from": "local"}

    server.add_middleware(ForwardMiddleware())
    server.calls = calls  # type: ignore[attr-defined]
    return server


@pytest.fixture
def serve():
    """Run a fake `start` over real HTTP on an ephemeral loopback port; returns the port."""
    running: list[tuple[uvicorn.Server, threading.Thread]] = []

    def _serve(server: FastMCP) -> int:
        config = uvicorn.Config(
            server.http_app(path="/mcp"), host="127.0.0.1", port=0, log_level="warning"
        )
        srv = uvicorn.Server(config)
        thread = threading.Thread(target=srv.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not srv.started:
            assert time.monotonic() < deadline, "fake start did not come up"
            time.sleep(0.01)
        running.append((srv, thread))
        return srv.servers[0].sockets[0].getsockname()[1]

    yield _serve
    for srv, thread in running:
        srv.should_exit = True
        thread.join(5)


def _announce(port: int, *, pid: int | None = None, version: str = VERSION) -> None:
    payload = {"pid": pid or os.getpid(), "port": port, "version": version}
    startfile.path().write_text(json.dumps(payload))


def _closed_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


async def _call(server: FastMCP, tool: str, args: dict):
    async with Client(server) as client:
        return await client.call_tool(tool, args, raise_on_error=False)


SEARCH = {"subject": "bia", "query": "q"}


async def test_model_tools_go_to_start_when_it_runs(local, serve):
    _announce(serve(_fake_start()))
    result = await _call(local, "search", SEARCH)
    assert result.structured_content == {"result": [{"from": "start"}]}
    tree = await _call(local, "submit_tree", {"subject": "bia", "tree": []})
    assert tree.structured_content == {"accepted": True, "from": "start"}
    assert local.calls == []


async def test_other_tools_stay_local(local, serve):
    _announce(serve(_fake_start()))
    result = await _call(local, "outline", {"subject": "bia"})
    assert result.structured_content == {"from": "local"}


async def test_no_runfile_runs_locally(local):
    result = await _call(local, "search", SEARCH)
    assert result.structured_content == {"result": [{"from": "local"}]}


async def test_stale_runfile_runs_locally_without_connecting(local, monkeypatch):
    class NeverClient(Client):
        async def __aenter__(self):
            raise AssertionError("connected to a dead start")

    monkeypatch.setattr("groundly.mcp.forward.Client", NeverClient)
    _announce(_closed_port(), pid=_dead_pid())
    result = await _call(local, "search", SEARCH)
    assert result.structured_content == {"result": [{"from": "local"}]}


async def test_version_mismatch_runs_locally(local, serve):
    _announce(serve(_fake_start(version="0.0.0")))
    result = await _call(local, "search", SEARCH)
    assert result.structured_content == {"result": [{"from": "local"}]}

    _announce(serve(_fake_start()), version="0.0.0")
    result = await _call(local, "search", SEARCH)
    assert result.structured_content == {"result": [{"from": "local"}]}


async def test_refused_connection_runs_locally(local):
    _announce(_closed_port())
    result = await _call(local, "search", SEARCH)
    assert result.structured_content == {"result": [{"from": "local"}]}


async def test_remote_tool_error_is_returned_not_retried(local, serve):
    _announce(serve(_fake_start()))
    args = {"subject": "x", "topic_id": 1, "questions": []}
    result = await _call(local, "submit_questions", args)
    assert result.is_error
    assert "unknown subject 'x'" in result.content[0].text
    assert local.calls == []


async def test_failure_after_send_is_an_error_not_a_local_retry(local, serve, monkeypatch):
    _announce(serve(_fake_start()))

    class DroppingClient(Client):
        async def call_tool_mcp(self, *a, **kw):
            raise httpx2.ReadError("connection dropped")

    monkeypatch.setattr("groundly.mcp.forward.Client", DroppingClient)
    result = await _call(local, "submit_tree", {"subject": "bia", "tree": []})
    assert result.is_error
    assert result.content[0].text == STOPPED_MESSAGE
    assert local.calls == []


async def test_forward_marks_its_requests(local, serve):
    seen: list = []
    _announce(serve(_fake_start(seen=seen)))
    await _call(local, "search", SEARCH)
    assert seen == ["1"]


async def test_windows_never_forwards(local, serve, monkeypatch):
    _announce(serve(_fake_start()))
    monkeypatch.setattr(startfile, "_WINDOWS", True)
    result = await _call(local, "search", SEARCH)
    assert result.structured_content == {"result": [{"from": "local"}]}


async def test_unresponsive_start_runs_locally(local, monkeypatch):
    # A stopped `start` (Ctrl-Z) still accepts connections but never answers the handshake;
    # nothing was sent, so the call is served here instead of hanging.
    import socket

    monkeypatch.setattr("groundly.mcp.forward.HANDSHAKE_TIMEOUT", 0.5)
    with socket.socket() as mute:
        mute.bind(("127.0.0.1", 0))
        mute.listen()
        _announce(mute.getsockname()[1])
        result = await _call(local, "search", SEARCH)
    assert result.structured_content == {"result": [{"from": "local"}]}
