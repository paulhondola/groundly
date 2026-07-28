"""groundly/agents/study_modes.py: drill_down/overview mirror ask()'s pipeline but
retrieve only from the graph arms, with no vector degrade — a missing graph is an
availability precondition (UC-12), not a routing decision, so GraphNotBuiltError
propagates uncaught."""

import json

import pytest
from llama_index.core.schema import NodeWithScore, TextNode

from groundly.agents.citations import NoCitationsError
from groundly.agents.study_modes import drill_down, overview
from groundly.core.paths import subject_dir
from groundly.core.progress import connect_progress
from groundly.retrieval.graph import GraphNotBuiltError


def _configure_chat(home):
    (home / "config.toml").write_text(
        '[providers.chat]\nbase_url = "http://x"\nmodel = "m"\napi_key = "sk"\n'
    )


def _traces(subject):
    conn = connect_progress(subject_dir(subject) / "progress.db")
    try:
        return conn.execute("SELECT * FROM traces ORDER BY id").fetchall()
    finally:
        conn.close()


def _graph_node(chunk_id, filename="lec.pdf"):
    return NodeWithScore(
        node=TextNode(
            text="graph text",
            id_=str(chunk_id),
            metadata={"chunk_id": chunk_id, "filename": filename, "page": 1, "heading_path": None},
        ),
        score=1.0,
    )


class _FakeGraphLocalRetriever:
    def __init__(self, subject):
        self.subject = subject
        self.path: list[str] = []

    def retrieve(self, query):
        self.path = ["graphrag-local", "entity-search"]
        return [_graph_node(1)]


class _FakeGraphGlobalRetriever:
    def __init__(self, subject):
        self.subject = subject
        self.path: list[str] = []
        self.communities: list[dict] = []

    def retrieve(self, query):
        self.path = ["graphrag-global", "community-search"]
        self.communities = [{"id": "0", "title": "Deadlocks"}]
        return [_graph_node(1)]


class _NotBuiltRetriever:
    def __init__(self, subject):
        self.subject = subject

    def retrieve(self, query):
        raise GraphNotBuiltError()


def test_drill_down_happy_path_returns_cited_answer_and_traces(
    retrievable_subject, monkeypatch, stub_chat
):
    home = subject_dir(retrievable_subject).parent
    _configure_chat(home)
    chat = stub_chat("Deadlocks need mutual exclusion [chunk 1].")
    monkeypatch.setattr("groundly.agents.study_modes.complete", chat)
    monkeypatch.setattr("groundly.agents.study_modes.GraphLocalRetriever", _FakeGraphLocalRetriever)

    result = drill_down(retrievable_subject, "deadlock")
    assert result.citations[0].chunk_id == 1
    assert result.citations[0].filename == "lec.pdf"

    rows = _traces(retrievable_subject)
    assert rows[-1]["kind"] == "ask"
    assert rows[-1]["arm"] == "drill_down"
    assert rows[-1]["outcome"] == "answered"
    assert json.loads(rows[-1]["citations"])[0]["chunk_id"] == 1


def test_overview_happy_path_names_communities_and_traces(
    retrievable_subject, monkeypatch, stub_chat
):
    home = subject_dir(retrievable_subject).parent
    _configure_chat(home)
    chat = stub_chat("The course broadly covers deadlocks [chunk 1].")
    monkeypatch.setattr("groundly.agents.study_modes.complete", chat)
    monkeypatch.setattr(
        "groundly.agents.study_modes.GraphGlobalRetriever", _FakeGraphGlobalRetriever
    )

    result = overview(retrievable_subject, "deadlocks")
    assert result.citations[0].chunk_id == 1
    assert result.communities == [{"id": "0", "title": "Deadlocks"}]

    rows = _traces(retrievable_subject)
    assert rows[-1]["arm"] == "overview"
    assert rows[-1]["outcome"] == "answered"


def test_drill_down_propagates_graph_not_built_error(subject, monkeypatch, stub_chat):
    home = subject_dir(subject).parent
    _configure_chat(home)
    chat = stub_chat("should never be called")
    monkeypatch.setattr("groundly.agents.study_modes.complete", chat)
    monkeypatch.setattr("groundly.agents.study_modes.GraphLocalRetriever", _NotBuiltRetriever)

    with pytest.raises(GraphNotBuiltError):
        drill_down(subject, "deadlock")
    assert chat.calls == []  # fails before any model call

    rows = _traces(subject)
    assert rows[-1]["outcome"] == "error"


def test_overview_propagates_graph_not_built_error(subject, monkeypatch, stub_chat):
    home = subject_dir(subject).parent
    _configure_chat(home)
    chat = stub_chat("should never be called")
    monkeypatch.setattr("groundly.agents.study_modes.complete", chat)
    monkeypatch.setattr("groundly.agents.study_modes.GraphGlobalRetriever", _NotBuiltRetriever)

    with pytest.raises(GraphNotBuiltError):
        overview(subject, "deadlocks")
    assert chat.calls == []

    rows = _traces(subject)
    assert rows[-1]["outcome"] == "error"


def test_drill_down_hallucinated_citation_raises_no_citations_error(
    retrievable_subject, monkeypatch, stub_chat
):
    home = subject_dir(retrievable_subject).parent
    _configure_chat(home)
    chat = stub_chat("Deadlocks need mutual exclusion [chunk 999].")
    monkeypatch.setattr("groundly.agents.study_modes.complete", chat)
    monkeypatch.setattr("groundly.agents.study_modes.GraphLocalRetriever", _FakeGraphLocalRetriever)

    with pytest.raises(NoCitationsError):
        drill_down(retrievable_subject, "deadlock")

    rows = _traces(retrievable_subject)
    assert rows[-1]["outcome"] == "error"
