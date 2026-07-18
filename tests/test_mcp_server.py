"""groundly/mcp/server.py: the FastMCP tool surface (list_subjects/search/ask/
get_page + citation resource) — thin wrappers over the same functions the CLI calls
(docs/superpowers/specs/2026-07-18-mcp-skeleton-design.md). Uses FastMCP's in-memory
Client: no subprocess servers, no network."""

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
