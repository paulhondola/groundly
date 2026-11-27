"""stdio forwarding: the three model tools go to a running `start`, everything else stays local,
and nothing is retried locally once a call may have reached `start`."""

import asyncio
import json
import os
import subprocess
import sys
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
    def submit_tree(subject: str, tree: list[dict], if_unmapped: bool = False) -> dict:
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


async def test_scoped_map_forces_first_map_when_forwarded_and_logs_commit(serve):
    from groundly.core import activity
    from groundly.mcp.activity import ActivityMiddleware
    from groundly.mcp.page_map import create_page_map_server

    remote = FastMCP("groundly", version=VERSION)
    remote.add_middleware(ActivityMiddleware("mcp_http"))
    received = []

    @remote.tool
    def submit_tree(subject: str, tree: list[dict], if_unmapped: bool = False) -> dict:
        received.append(if_unmapped)
        return {"accepted": True}

    _announce(serve(remote))
    scoped = create_page_map_server("bia")
    scoped.add_middleware(ActivityMiddleware("mcp_stdio", page_run=True))
    scoped.add_middleware(ForwardMiddleware(page_run=True))
    args = {"subject": "bia", "tree": [{"title": "Example"}], "if_unmapped": False}
    result = await _call(scoped, "submit_tree", args)
    assert result.structured_content == {"accepted": True}
    assert received == [True]
    recent = activity.snapshot()["recent"]
    assert len(recent) == 1
    assert recent[0]["subject"] == "bia"
    public = FastMCP("groundly")
    public.add_middleware(ActivityMiddleware("mcp_stdio"))
    public.add_middleware(ForwardMiddleware())

    @public.tool(name="submit_tree")
    def must_forward(subject: str, tree: list[dict], if_unmapped: bool = False) -> dict:
        raise AssertionError("the conditional write should use start")

    result = await _call(public, "submit_tree", {**args, "subject": "other", "if_unmapped": True})
    assert result.structured_content == {"accepted": True}
    assert received == [True, True]
    events = activity.snapshot()["recent"]
    assert [event["subject"] for event in events] == ["other", "bia"]
    (external,) = [host for host in activity.snapshot()["hosts"] if host["subject"] == "other"]
    assert external["client"] != "Groundly page run"


async def test_same_version_start_without_first_map_condition_is_skipped(local, serve):
    _announce(serve(_fake_start()))
    result = await _call(local, "submit_tree", {"subject": "bia", "tree": [], "if_unmapped": True})
    assert result.structured_content == {"accepted": True, "from": "local"}
    assert local.calls == ["submit_tree"]


async def test_start_reports_a_first_map_that_commits_after_its_caller_exits(serve):
    from groundly.core import activity
    from groundly.mcp.activity import ActivityMiddleware

    remote = FastMCP("groundly", version=VERSION)
    remote.add_middleware(ActivityMiddleware("mcp_http"))
    entered, release = threading.Event(), threading.Event()

    @remote.tool
    def submit_tree(subject: str, tree: list[dict], if_unmapped: bool = False) -> dict:
        entered.set()
        if not release.wait(5):
            raise TimeoutError
        return {"accepted": True}

    _announce(serve(remote))
    caller = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        """
from fastmcp import Client
from groundly.mcp.forward import ForwardMiddleware
from groundly.mcp.page_map import create_page_map_server
import asyncio

async def run():
    scoped = create_page_map_server("bia")
    scoped.add_middleware(ForwardMiddleware(page_run=True))
    async with Client(scoped) as client:
        await client.call_tool("submit_tree", {"subject": "bia", "tree": [{"title": "Locks"}]})

asyncio.run(run())
""",
    )
    try:
        assert await asyncio.to_thread(entered.wait, 4)
        caller.kill()
        await caller.wait()
        assert activity.snapshot()["recent"] == []
        release.set()
        for _ in range(100):
            if activity.snapshot()["recent"]:
                break
            await asyncio.sleep(0.02)
        assert [event["subject"] for event in activity.snapshot()["recent"]] == ["bia"]
    finally:
        release.set()
        if caller.returncode is None:
            caller.kill()
            await caller.wait()


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
