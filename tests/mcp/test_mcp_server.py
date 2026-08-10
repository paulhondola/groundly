"""groundly/mcp/server.py: the FastMCP tool surface (list_subjects/search/ask/
get_page + citation resource) — thin wrappers over the same functions the CLI calls
(docs/superpowers/specs/2026-07-18-mcp-skeleton-design.md). Uses FastMCP's in-memory
Client: no subprocess servers, no network."""

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


def _configure_chat(subject_name):
    (subject_dir(subject_name).parent / "config.toml").write_text(
        '[providers.chat]\nbase_url = "http://x"\nmodel = "m"\n'
    )


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
    for mod in ("sentence_transformers", "torch", "FlagEmbedding"):
        sys.modules.pop(mod, None)
    for mod in list(sys.modules):
        if mod == "groundly.mcp.server" or mod.startswith("groundly.mcp.server."):
            del sys.modules[mod]

    import groundly.mcp.server  # noqa: F401

    assert "sentence_transformers" not in sys.modules
    assert "torch" not in sys.modules
    assert "FlagEmbedding" not in sys.modules


def test_importing_server_never_pulls_in_graphrag():
    """The graph stack is the other half of spawn cost, and the easy one to reintroduce
    by accident: `_maps_service_errors` needs `GraphNotBuiltError`, which lives in
    retrieval/graph.py behind graphrag and pandas. Hoisting that import to module scope
    to tidy the decorator would look harmless and would put the whole graph stack on
    every host handshake.

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
    assert top["filename"] == "lec.pdf"
    assert "chunk_id" in top and "text" in top and "score" in top and "heading_path" in top
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


# --- ask --------------------------------------------------------------------------


async def test_ask_happy_path_returns_answer_and_citations(
    retrievable_subject, monkeypatch, stub_chat
):
    _configure_chat(retrievable_subject)
    chat = stub_chat("Deadlocks need mutual exclusion [chunk 1].")
    monkeypatch.setattr("groundly.agents.ask.complete", chat)

    async with Client(mcp) as client:
        result = await client.call_tool(
            "ask", {"subject": "TEST", "query": "what causes a deadlock?"}
        )
    assert "mutual exclusion" in result.data["answer"]
    assert result.data["citations"][0]["chunk_id"] == 1
    assert result.data["citations"][0]["filename"] == "lec.pdf"
    assert result.data["citations"][0]["uri"] == "groundly://TEST/lec.pdf#page=1"


async def test_ask_unknown_subject_errors(subject_free_home):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="unknown subject 'NOPE'"):
            await client.call_tool("ask", {"subject": "NOPE", "query": "q"})


async def test_ask_no_provider_fails_with_specific_message_while_search_works(
    retrievable_subject,
):
    # zero-key: UC-02 criterion — ask needs a provider, search does not, same subject
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="ask needs a configured chat provider"):
            await client.call_tool("ask", {"subject": "TEST", "query": "what is a deadlock?"})
        search_result = await client.call_tool("search", {"subject": "TEST", "query": "deadlock"})
    assert search_result.data


async def test_ask_hallucinated_citation_raises_tool_error(
    retrievable_subject, monkeypatch, stub_chat
):
    _configure_chat(retrievable_subject)
    chat = stub_chat("Deadlocks need mutual exclusion [chunk 999].")
    monkeypatch.setattr("groundly.agents.ask.complete", chat)

    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="no chunk ids that resolve"):
            await client.call_tool("ask", {"subject": "TEST", "query": "what causes a deadlock?"})


async def test_ask_refusal_returns_no_citations(retrievable_subject, monkeypatch, stub_chat):
    _configure_chat(retrievable_subject)
    chat = stub_chat("not covered by the course materials")
    monkeypatch.setattr("groundly.agents.ask.complete", chat)

    async with Client(mcp) as client:
        result = await client.call_tool(
            "ask", {"subject": "TEST", "query": "what is the capital of France?"}
        )
    assert result.data["answer"] == "not covered by the course materials"
    assert result.data["citations"] == []


async def test_ask_model_download_error_raises_tool_error(retrievable_subject, monkeypatch):
    _configure_chat(retrievable_subject)
    from groundly.llm.embeddings import ModelDownloadError

    def fake_ask(*a, **k):
        raise ModelDownloadError("failed to load bge-m3: boom")

    monkeypatch.setattr("groundly.agents.ask.ask", fake_ask)
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="failed to load bge-m3"):
            await client.call_tool("ask", {"subject": "TEST", "query": "q"})


async def test_ask_chat_unreachable_error_raises_tool_error(retrievable_subject, monkeypatch):
    _configure_chat(retrievable_subject)
    from groundly.llm.chat import ChatUnreachableError

    def fake_ask(*a, **k):
        raise ChatUnreachableError("[providers.chat] at http://x is unreachable: boom")

    monkeypatch.setattr("groundly.agents.ask.ask", fake_ask)
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="unreachable"):
            await client.call_tool("ask", {"subject": "TEST", "query": "q"})


async def test_search_model_download_error_raises_tool_error(retrievable_subject, monkeypatch):
    from groundly.llm.embeddings import ModelDownloadError

    def fake_search(*a, **k):
        raise ModelDownloadError("failed to load bge-m3: boom")

    monkeypatch.setattr("groundly.retrieval.vector.search", fake_search)
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="failed to load bge-m3"):
            await client.call_tool("search", {"subject": "TEST", "query": "q"})


async def test_mcp_ask_matches_cli_ask_for_the_same_query(
    retrievable_subject, monkeypatch, stub_chat
):
    # UC-02 equivalence: both surfaces call the exact same groundly.agents.ask.ask()
    _configure_chat(retrievable_subject)
    chat = stub_chat("Deadlocks need mutual exclusion [chunk 1].")
    monkeypatch.setattr("groundly.agents.ask.complete", chat)

    from groundly.agents.ask import ask as direct_ask

    direct_result = direct_ask("TEST", "what causes a deadlock?")

    async with Client(mcp) as client:
        mcp_result = await client.call_tool(
            "ask", {"subject": "TEST", "query": "what causes a deadlock?"}
        )
    assert mcp_result.data["answer"] == direct_result.answer
    assert [c["chunk_id"] for c in mcp_result.data["citations"]] == [
        c.chunk_id for c in direct_result.citations
    ]


# --- drill_down / overview -----------------------------------------------------------


class _FakeGraphLocalRetriever:
    def __init__(self, subject):
        self.subject = subject
        self.path: list[str] = []

    def retrieve(self, query):
        from llama_index.core.schema import NodeWithScore, TextNode

        self.path = ["graphrag-local", "entity-search"]
        node = TextNode(
            text="graph text",
            id_="1",
            metadata={"chunk_id": 1, "filename": "lec.pdf", "page": 1, "heading_path": None},
        )
        return [NodeWithScore(node=node, score=1.0)]


class _FakeGraphGlobalRetriever:
    def __init__(self, subject):
        self.subject = subject
        self.path: list[str] = []
        self.communities: list[dict] = []

    def retrieve(self, query):
        from llama_index.core.schema import NodeWithScore, TextNode

        self.path = ["graphrag-global", "community-search"]
        self.communities = [{"id": "0", "title": "Deadlocks"}]
        node = TextNode(
            text="graph text",
            id_="1",
            metadata={"chunk_id": 1, "filename": "lec.pdf", "page": 1, "heading_path": None},
        )
        return [NodeWithScore(node=node, score=1.0)]


async def test_drill_down_happy_path_returns_answer_and_citations(
    retrievable_subject, monkeypatch, stub_chat
):
    _configure_chat(retrievable_subject)
    chat = stub_chat("Deadlocks need mutual exclusion [chunk 1].")
    monkeypatch.setattr("groundly.agents.study_modes.complete", chat)
    monkeypatch.setattr("groundly.agents.study_modes.GraphLocalRetriever", _FakeGraphLocalRetriever)

    async with Client(mcp) as client:
        result = await client.call_tool("drill_down", {"subject": "TEST", "entity": "deadlock"})
    assert "mutual exclusion" in result.data["answer"]
    assert result.data["citations"][0]["chunk_id"] == 1
    assert result.data["citations"][0]["filename"] == "lec.pdf"
    assert result.data["citations"][0]["uri"] == "groundly://TEST/lec.pdf#page=1"


async def test_drill_down_unknown_subject_errors(subject_free_home):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="unknown subject 'NOPE'"):
            await client.call_tool("drill_down", {"subject": "NOPE", "entity": "e"})


async def test_drill_down_graph_not_built_raises_tool_error(retrievable_subject):
    _configure_chat(retrievable_subject)
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="graph not built"):
            await client.call_tool("drill_down", {"subject": "TEST", "entity": "deadlock"})


async def test_drill_down_no_provider_fails_with_specific_message(retrievable_subject):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="drill_down needs a configured chat provider"):
            await client.call_tool("drill_down", {"subject": "TEST", "entity": "deadlock"})


async def test_overview_happy_path_returns_answer_citations_and_communities(
    retrievable_subject, monkeypatch, stub_chat
):
    _configure_chat(retrievable_subject)
    chat = stub_chat("The course broadly covers deadlocks [chunk 1].")
    monkeypatch.setattr("groundly.agents.study_modes.complete", chat)
    monkeypatch.setattr(
        "groundly.agents.study_modes.GraphGlobalRetriever", _FakeGraphGlobalRetriever
    )

    async with Client(mcp) as client:
        result = await client.call_tool("overview", {"subject": "TEST", "topic": "deadlocks"})
    assert "deadlocks" in result.data["answer"]
    assert result.data["citations"][0]["chunk_id"] == 1
    assert result.data["citations"][0]["uri"] == "groundly://TEST/lec.pdf#page=1"
    assert result.data["communities"] == [{"id": "0", "title": "Deadlocks"}]


async def test_overview_unknown_subject_errors(subject_free_home):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="unknown subject 'NOPE'"):
            await client.call_tool("overview", {"subject": "NOPE", "topic": "t"})


async def test_overview_graph_not_built_raises_tool_error(retrievable_subject):
    _configure_chat(retrievable_subject)
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="graph not built"):
            await client.call_tool("overview", {"subject": "TEST", "topic": "deadlocks"})


async def test_overview_no_provider_fails_with_specific_message(retrievable_subject):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="overview needs a configured chat provider"):
            await client.call_tool("overview", {"subject": "TEST", "topic": "deadlocks"})


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


# --- generate_deck / get_job / list_decks (thick door) -------------------------------


async def test_generate_deck_without_confirm_returns_estimate_and_starts_nothing(
    retrievable_subject,
):
    from groundly.agents import jobs

    before = dict(jobs._JOBS)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "generate_deck",
            {"subject": "TEST", "topic": "deadlocks", "deck": "OS Deck"},
        )
    assert "estimated_tokens" in result.data
    assert "confirm" in result.data["note"]
    assert jobs._JOBS == before  # no job registered


async def test_generate_deck_confirm_without_provider_fails_with_specific_message(
    retrievable_subject,
):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=r"generate_deck needs a configured generation"):
            await client.call_tool(
                "generate_deck",
                {"subject": "TEST", "topic": "deadlocks", "deck": "OS Deck", "confirm": True},
            )


async def test_get_job_unknown_id_errors_with_session_scope_explanation():
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="do not survive a server restart"):
            await client.call_tool("get_job", {"job_id": "nope"})


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
    # patch the class, not the module-level `mcp` instance: the heavy-imports test
    # reloads groundly.mcp.server, so serve()'s lazy import may see a fresh instance
    monkeypatch.setattr(FastMCP, "run", lambda self, **kw: calls.update(kw))
    result = CliRunner().invoke(app, ["serve", "--port", "5150"])
    assert result.exit_code == 0
    assert calls == {
        "transport": "http",
        "host": "127.0.0.1",
        "port": 5150,
        "host_origin_protection": "auto",
    }
