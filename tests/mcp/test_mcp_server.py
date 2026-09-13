"""groundly/mcp/server.py: the FastMCP tool surface (list_subjects/search/get_page/
submit_cards/list_decks/export_deck + citation resource)."""

import subprocess
import sys

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from groundly.core.paths import subject_dir
from groundly.mcp.server import mcp


class _NearEmbedder:
    from groundly.core.manifest import EMBEDDING_DIM

    def encode(self, texts):
        return [[1.0, 0.0] + [0.0] * (self.EMBEDDING_DIM - 2) for _ in texts], [
            {1: 1.0} for _ in texts
        ]


class _PassthroughReranker:
    """Preserves the fused (best-first) order instead of exercising real rerank math —
    MCP's `search`/`ask` tools don't expose a `--no-rerank` escape hatch (design table),
    so tests stub the reranker the same way test_cli_ask.py stubs the embedder."""

    def compute_score(self, pairs):
        return list(range(len(pairs), 0, -1))


@pytest.fixture(autouse=True)
def _stub_models(monkeypatch):
    monkeypatch.setattr("groundly.llm.embeddings.BgeM3Embedder", _NearEmbedder)
    monkeypatch.setattr("groundly.llm.rerank.BgeReranker", _PassthroughReranker)


@pytest.fixture
def subject_free_home(monkeypatch, tmp_path):
    """GROUNDLY_HOME with no subjects at all — for list_subjects-empty and
    unknown-subject error cases."""
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


# --- spawn speed ----------------------------------------------------------------


def test_importing_server_never_pulls_in_heavy_ml_deps():
    """A subprocess rather than sys.modules surgery, for the reason spelled out below and
    one of its own: popping `torch` does not unload torch's C++ extension, it only makes
    the next real import re-run `torch/__init__.py`, which then dies re-registering a
    process-global TORCH_LIBRARY namespace. The in-process version of this test poisoned
    every later test that loads bge-m3 for real — invisibly, since whether it ran before
    them depended on file ordering."""
    probe = (
        "import sys, groundly.mcp.server;"
        "print(','.join(m for m in ('sentence_transformers', 'torch', 'FlagEmbedding')"
        " if m in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "", f"ML deps imported at MCP spawn: {result.stdout.strip()}"


def test_importing_server_never_pulls_in_graphrag():
    """The graph stack is the other half of spawn cost, and the easy one to reintroduce
    by accident: a heavy import hoisted to module scope to "tidy" some helper would look
    harmless and would put the whole graph stack on every host handshake.

    A subprocess rather than sys.modules surgery: popping `graphrag` mid-session while
    its submodules stay loaded leaves a half-initialized package, and
    `allow_nonstandard_service_tier`'s idempotence flag would then claim a patch that a
    re-imported `graphrag_llm` no longer carries.
    """
    probe = (
        "import sys, groundly.mcp.server;"
        "print(','.join(m for m in ('graphrag', 'pandas', 'torch') if m in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "", f"heavy deps imported at MCP spawn: {result.stdout.strip()}"


# --- tool surface as UX -------------------------------------------------------------
# `.claude/rules/conventions.md`: tool descriptions are UX, written for the host model.
# Nothing asserted that until decision 30 measured what the old wording cost — a host told
# nothing about retrieval called `search` on 8 of 48 apd questions and 0 of 17 factoids.
# These two pin the trigger that fixes it, the same way test_grounding.py pins the eval's
# condition prompts: an edit may reword them, but not quietly delete them.


async def test_handshake_tells_the_host_to_retrieve_before_answering():
    async with Client(mcp) as client:
        instructions = client.initialize_result.instructions
    assert instructions, "the server advertises no instructions — the one server-wide trigger"
    lowered = instructions.lower()
    assert "retrieve before you answer" in lowered
    assert "not a substitute" in lowered, (
        "the instructions must say model knowledge does not substitute for the course's "
        "own treatment — that is what the 0-of-17 factoid failure needed to hear"
    )


async def test_search_description_leads_with_when_to_use_not_retrieval_mechanics():
    async with Client(mcp) as client:
        search_tool = next(t for t in await client.list_tools() if t.name == "search")
    description = search_tool.description.lower()
    assert "use it for any question" in description, "no trigger clause — the old wording's defect"
    assert "already" in description and "know" in description, (
        "the description must cover questions the model already knows the answer to; "
        "those were the ones it never retrieved for"
    )
    assert "use `ask` when you need" not in description, (
        "the old redirect sent a host that is allowlisted to `search` alone toward a tool "
        "it cannot call, leaving it with model knowledge as the only option"
    )


async def test_the_tool_surface_is_exactly_these_seven_entries():
    """The surface is UX (.claude/rules/conventions.md). A tool that creeps back in —
    or one quietly dropped — should fail here rather than in a host months later."""
    async with Client(mcp) as client:
        tools = {t.name for t in await client.list_tools()}
        templates = {t.uriTemplate for t in await client.list_resource_templates()}
    assert tools == {
        "list_subjects",
        "search",
        "get_page",
        "submit_cards",
        "list_decks",
        "export_deck",
    }
    assert templates == {"groundly://{subject}/{filename}"}


# --- list_subjects ----------------------------------------------------------------


async def test_list_subjects_reports_counts_and_graph_built(retrievable_subject):
    async with Client(mcp) as client:
        result = await client.call_tool("list_subjects", {})
    assert result.data == [
        {
            "subject": "TEST",
            "materials": 1,
            "pages": 3,
            "chunks": 3,
            "graph_built": False,
        }
    ]


async def test_list_subjects_empty_when_no_subjects(subject_free_home):
    async with Client(mcp) as client:
        result = await client.call_tool("list_subjects", {})
    assert result.data == []


# --- search ------------------------------------------------------------------------


async def test_search_happy_path_returns_ranked_chunks_with_uri(retrievable_subject):
    async with Client(mcp) as client:
        result = await client.call_tool("search", {"subject": "TEST", "query": "deadlock", "k": 3})
    assert result.data
    top = result.data[0]
    assert set(top) == {
        "chunk_id",
        "text",
        "score",
        "filename",
        "page",
        "heading_path",
        "uri",
    }
    assert top["filename"] == "lec.pdf"
    assert top["uri"] == f"groundly://TEST/lec.pdf#page={top['page']}"


async def test_search_unknown_subject_errors(subject_free_home):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="unknown subject 'NOPE'"):
            await client.call_tool("search", {"subject": "NOPE", "query": "q"})


async def test_search_works_with_no_provider_configured(retrievable_subject):
    # zero-key: search never requires [providers.chat] at all
    async with Client(mcp) as client:
        result = await client.call_tool("search", {"subject": "TEST", "query": "deadlock"})
    assert result.data


async def test_search_model_download_error_raises_tool_error(retrievable_subject, monkeypatch):
    from groundly.llm.embeddings import ModelDownloadError

    def fake_search(*a, **k):
        raise ModelDownloadError("failed to load bge-m3: boom")

    monkeypatch.setattr("groundly.retrieval.vector.search", fake_search)
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="failed to load bge-m3"):
            await client.call_tool("search", {"subject": "TEST", "query": "q"})


# --- submit_cards (thin door) --------------------------------------------------------


async def test_submit_cards_round_trip_with_no_provider_configured(retrievable_subject):
    """The zero-key proof: host generates, groundly verifies+stores — no [providers]
    section exists anywhere in this test's GROUNDLY_HOME."""
    cards = [
        {"front": "What does deadlock need?", "back": "mutual exclusion", "chunk_ids": [1]},
        {"front": "bogus", "back": "unsupported", "chunk_ids": [999]},
    ]
    async with Client(mcp) as client:
        result = await client.call_tool(
            "submit_cards", {"subject": "TEST", "deck": "OS Deck", "cards": cards}
        )
    assert result.data["deck"] == "OS Deck"
    assert [a["index"] for a in result.data["accepted"]] == [0]
    assert result.data["accepted"][0]["question_id"] is not None
    rejected = result.data["rejected"]
    assert len(rejected) == 1 and rejected[0]["index"] == 1
    assert rejected[0]["reason"] == "not_answerable_from_chunks"
    assert "999" in rejected[0]["detail"]


async def test_submit_cards_caps_batch_size(retrievable_subject):
    from groundly.agents.decks import MAX_COUNT

    too_many = [{"front": f"f{i}", "back": "b", "chunk_ids": [1]} for i in range(MAX_COUNT + 1)]
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="at most 50 cards per call"):
            await client.call_tool(
                "submit_cards", {"subject": "TEST", "deck": "OS Deck", "cards": too_many}
            )


async def test_submit_cards_hostile_deck_name_rejected(retrievable_subject):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="invalid deck name"):
            await client.call_tool(
                "submit_cards",
                {
                    "subject": "TEST",
                    "deck": "../escape",
                    "cards": [{"front": "f", "back": "b", "chunk_ids": [1]}],
                },
            )


async def test_submit_cards_unknown_subject_errors(subject_free_home):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="unknown subject"):
            await client.call_tool(
                "submit_cards",
                {"subject": "NOPE", "deck": "D", "cards": []},
            )


# --- list_decks ------------------------------------------------------------------


async def test_list_decks_reports_names_and_counts(retrievable_subject):
    async with Client(mcp) as client:
        await client.call_tool(
            "submit_cards",
            {
                "subject": "TEST",
                "deck": "OS Deck",
                "cards": [
                    {
                        "front": "What does deadlock need?",
                        "back": "mutual exclusion",
                        "chunk_ids": [1],
                    }
                ],
            },
        )
        result = await client.call_tool("list_decks", {"subject": "TEST"})
    assert result.data == [{"deck": "OS Deck", "cards": 1}]


# --- export_deck ---------------------------------------------------------------------


async def test_export_deck_returns_path_under_subject_exports(retrievable_subject):
    from pathlib import Path

    async with Client(mcp) as client:
        await client.call_tool(
            "submit_cards",
            {
                "subject": "TEST",
                "deck": "OS Deck",
                "cards": [
                    {
                        "front": "What does deadlock need?",
                        "back": "mutual exclusion",
                        "chunk_ids": [1],
                    }
                ],
            },
        )
        result = await client.call_tool("export_deck", {"subject": "TEST", "deck": "OS Deck"})
    path = Path(result.data["path"])
    assert path.exists()
    assert path == subject_dir("TEST") / "exports" / "OS Deck.apkg"


async def test_export_deck_empty_deck_errors_with_named_cause(retrievable_subject):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="has no cards"):
            await client.call_tool("export_deck", {"subject": "TEST", "deck": "Nope"})


# --- get_page -----------------------------------------------------------------------


async def test_get_page_happy_path_returns_verbatim_chunks_in_order(retrievable_subject):
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_page", {"subject": "TEST", "filename": "lec.pdf", "page": 1}
        )
    assert result.data == [
        {
            "chunk_id": 1,
            "text": "deadlock needs mutual exclusion to occur",
            "heading_path": "Intro > Deadlocks",
        }
    ]


async def test_get_page_no_match_returns_empty_list(retrievable_subject):
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_page", {"subject": "TEST", "filename": "lec.pdf", "page": 999}
        )
    assert result.data == []


async def test_get_page_unknown_subject_errors(subject_free_home):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="unknown subject 'NOPE'"):
            await client.call_tool(
                "get_page", {"subject": "NOPE", "filename": "lec.pdf", "page": 1}
            )


# --- citation resource ---------------------------------------------------------------


async def test_resource_groups_chunks_by_page(retrievable_subject):
    async with Client(mcp) as client:
        contents = await client.read_resource("groundly://TEST/lec.pdf")
    import json

    body = json.loads(contents[0].text)
    assert set(body.keys()) == {"1", "2", "3"}
    assert body["1"][0]["text"] == "deadlock needs mutual exclusion to occur"


async def test_resource_fragment_is_glued_onto_filename_not_split_by_fastmcp(
    retrievable_subject,
):
    # Empirically verified (see design doc): FastMCP does not parse `#page=N` out as a
    # separate handler argument — it arrives concatenated onto the last path param.
    # The resource handler parses it back out itself and narrows to that one page.
    import json

    async with Client(mcp) as client:
        contents = await client.read_resource("groundly://TEST/lec.pdf#page=2")
    body = json.loads(contents[0].text)
    assert set(body.keys()) == {"2"}
    assert body["2"][0]["text"] == "semaphores and mutexes for synchronization"


def test_citation_uri_omits_fragment_for_pageless_chunks():
    # plain-text/MD materials index with NULL pages — no "#page=None" in their URIs
    from groundly.mcp.server import _citation_uri

    assert _citation_uri("TEST", "notes.txt", None) == "groundly://TEST/notes.txt"
    assert _citation_uri("TEST", "lec.pdf", 2) == "groundly://TEST/lec.pdf#page=2"


# --- http transport (`groundly serve`) ----------------------------------------------


async def test_http_transport_serves_the_same_tools(retrievable_subject):
    # smoke test for `groundly serve`: same FastMCP instance, Streamable HTTP transport
    import asyncio
    import threading

    import uvicorn

    app = mcp.http_app(host_origin_protection="auto")  # mirror cli/serve.py's kwargs
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    sock = config.bind_socket()  # ephemeral port, bound before the thread starts
    port = sock.getsockname()[1]
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        for _ in range(500):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started, "uvicorn never came up"

        async with Client(f"http://127.0.0.1:{port}/mcp") as client:
            result = await client.call_tool("list_subjects", {})
        assert result.data == [
            {
                "subject": "TEST",
                "materials": 1,
                "pages": 3,
                "chunks": 3,
                "graph_built": False,
            }
        ]

        # DNS-rebinding guard: a hostile Host header must be rejected (421), not served
        import httpx

        rebound = await httpx.AsyncClient().post(
            f"http://127.0.0.1:{port}/mcp",
            json={"jsonrpc": "2.0", "method": "ping", "id": 1},
            headers={
                "Host": "evil.example.com",
                "Accept": "application/json, text/event-stream",
            },
        )
        assert rebound.status_code == 421
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_serve_cli_wires_http_transport_with_rebinding_protection(monkeypatch):
    """`groundly serve` must pass the exact production kwargs to run() — the smoke
    test above exercises the ASGI app; this covers cli/serve.py's own run() line
    (transport string, loopback host, host_origin_protection)."""
    from typer.testing import CliRunner

    import groundly.cli.serve  # noqa: F401  — registers the verb on the app
    from fastmcp import FastMCP
    from groundly.cli.app import app

    calls: dict = {}
    # patch the class, not the module-level `mcp` instance: `run` is inherited from a
    # fastmcp mixin, so an instance patch cannot be undone — teardown writes it into
    # `mcp.__dict__`, where it shadows this one and serve() boots a real server that
    # blocks forever. tests/conftest.py fails whichever test leaks it.
    monkeypatch.setattr(FastMCP, "run", lambda self, **kw: calls.update(kw))
    result = CliRunner().invoke(app, ["serve", "--port", "5150"])
    assert result.exit_code == 0
    assert calls == {
        "transport": "http",
        "host": "127.0.0.1",
        "port": 5150,
        "host_origin_protection": "auto",
    }
