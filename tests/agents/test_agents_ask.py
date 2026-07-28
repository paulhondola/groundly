"""groundly/agents/ask.py: router -> retrieval -> assemble -> chat -> citation
resolution -> trace row, for every outcome (UC-02)."""

import json

import pytest
from llama_index.core.schema import NodeWithScore, TextNode

from groundly.agents.ask import NoCitationsError, ask
from groundly.core.paths import subject_dir
from groundly.core.progress import connect_progress
from groundly.llm.config import ProviderNotConfiguredError
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


def _near_embedder():
    from groundly.core.manifest import EMBEDDING_DIM

    class E:
        def encode(self, texts):
            return [[1.0, 0.0] + [0.0] * (EMBEDDING_DIM - 2) for _ in texts], [
                {1: 1.0} for _ in texts
            ]

    return E()


def test_ask_happy_path_returns_cited_answer_and_traces_answered(
    retrievable_subject, monkeypatch, stub_chat
):
    home = subject_dir(retrievable_subject).parent
    _configure_chat(home)
    chat = stub_chat("Deadlocks need mutual exclusion [chunk 1].")
    monkeypatch.setattr("groundly.agents.ask.complete", chat)
    result = ask(
        retrievable_subject, "what causes a deadlock?", embedder=_near_embedder(), rerank=False
    )
    assert result.citations
    assert result.citations[0].chunk_id == 1
    assert result.citations[0].filename == "lec.pdf"
    assert "[chunk 1]" in result.answer

    rows = _traces(retrievable_subject)
    assert rows[-1]["kind"] == "ask"
    assert rows[-1]["outcome"] == "answered"
    assert json.loads(rows[-1]["citations"])[0]["chunk_id"] == 1


def test_ask_hallucinated_citation_raises_and_traces_error(
    retrievable_subject, monkeypatch, stub_chat
):
    home = subject_dir(retrievable_subject).parent
    _configure_chat(home)
    chat = stub_chat("Deadlocks need mutual exclusion [chunk 999].")
    monkeypatch.setattr("groundly.agents.ask.complete", chat)
    with pytest.raises(NoCitationsError):
        ask(retrievable_subject, "what causes a deadlock?", embedder=_near_embedder(), rerank=False)

    rows = _traces(retrievable_subject)
    assert rows[-1]["outcome"] == "error"
    assert rows[-1]["error"]


def test_ask_refusal_needs_no_citations_and_traces_refused(
    retrievable_subject, monkeypatch, stub_chat
):
    home = subject_dir(retrievable_subject).parent
    _configure_chat(home)
    chat = stub_chat("not covered by the course materials")
    monkeypatch.setattr("groundly.agents.ask.complete", chat)
    result = ask(
        retrievable_subject,
        "what is the capital of France?",
        embedder=_near_embedder(),
        rerank=False,
    )
    assert result.answer == "not covered by the course materials"
    assert result.citations == []

    rows = _traces(retrievable_subject)
    assert rows[-1]["outcome"] == "refused"


def test_ask_no_key_fails_before_any_model_load(subject, monkeypatch):
    def must_not_encode(*a, **k):
        raise AssertionError("embedder must never be constructed without a chat provider")

    with pytest.raises(ProviderNotConfiguredError) as exc:
        ask(subject, "q", embedder=must_not_encode)
    assert "[providers.chat]" in str(exc.value)
    assert _traces(subject) == []  # nothing started, nothing to trace


def test_ask_empty_store_refuses_without_llm_call(subject, monkeypatch, stub_chat):
    home = subject_dir(subject).parent
    _configure_chat(home)
    chat = stub_chat("should never be called")
    monkeypatch.setattr("groundly.agents.ask.complete", chat)
    result = ask(subject, "what is a deadlock?", embedder=_near_embedder(), rerank=False)
    assert result.answer == "not covered by the course materials"
    assert chat.calls == []  # empty store refuses before any chat call (router unconfigured too)

    rows = _traces(subject)
    assert rows[-1]["outcome"] == "refused"


def test_ask_router_configured_logs_label(retrievable_subject, monkeypatch, stub_chat):
    home = subject_dir(retrievable_subject).parent
    _configure_chat(home)
    # classify() itself is unit-tested in test_agents_router.py; here only the
    # plumbing (label flows through to AskResult + trace) is under test.
    chat = stub_chat("A deadlock needs mutual exclusion [chunk 1].")
    monkeypatch.setattr("groundly.agents.ask.classify", lambda query, c: "factoid")
    monkeypatch.setattr("groundly.agents.ask.complete", chat)
    result = ask(
        retrievable_subject, "what causes a deadlock?", embedder=_near_embedder(), rerank=False
    )
    assert result.router_label == "factoid"

    rows = _traces(retrievable_subject)
    assert rows[-1]["router_label"] == "factoid"


def test_ask_router_unconfigured_logs_null_label(retrievable_subject, monkeypatch, stub_chat):
    home = subject_dir(retrievable_subject).parent
    _configure_chat(home)
    chat = stub_chat("A deadlock needs mutual exclusion [chunk 1].")
    monkeypatch.setattr("groundly.agents.ask.complete", chat)
    result = ask(
        retrievable_subject, "what causes a deadlock?", embedder=_near_embedder(), rerank=False
    )
    assert result.router_label is None

    rows = _traces(retrievable_subject)
    assert rows[-1]["router_label"] is None


# --- router-label -> arm-selection matrix (P5) ---------------------------------


def _graph_node(chunk_id):
    return NodeWithScore(
        node=TextNode(
            text="graph text",
            id_=str(chunk_id),
            metadata={
                "chunk_id": chunk_id,
                "filename": "lec.pdf",
                "page": 1,
                "heading_path": None,
            },
        ),
        score=1.0,
    )


class _FakeGraphLocalRetriever:
    """Stubs `GraphLocalRetriever` at ask.py's import site — always returns chunk 2,
    never runs real graphrag."""

    instances: list["_FakeGraphLocalRetriever"] = []

    def __init__(self, subject):
        self.subject = subject
        self.path: list[str] = []
        _FakeGraphLocalRetriever.instances.append(self)

    def retrieve(self, query):
        self.path = ["graphrag-local", "entity-search"]
        return [_graph_node(2)]


class _FakeGraphGlobalRetriever:
    """Stubs `GraphGlobalRetriever` at ask.py's import site."""

    instances: list["_FakeGraphGlobalRetriever"] = []

    def __init__(self, subject):
        self.subject = subject
        self.path: list[str] = []
        self.communities: list[dict] = []
        _FakeGraphGlobalRetriever.instances.append(self)

    def retrieve(self, query):
        self.path = ["graphrag-global", "community-search"]
        self.communities = [{"id": "0", "title": "Deadlocks"}]
        return [_graph_node(2)]


class _NotBuiltRetriever:
    """Stubs either graph retriever to simulate a subject with no graph built yet."""

    def __init__(self, subject):
        self.subject = subject

    def retrieve(self, query):
        raise GraphNotBuiltError()


def _no_vector_retrieval(monkeypatch):
    """Fails the test loudly if the vector arm is ever asked to retrieve — used to
    assert graph-only arms don't fall back to vector unless degrading."""

    def _must_not_retrieve(self, query):
        raise AssertionError("vector retriever must not run for this arm")

    monkeypatch.setattr("groundly.retrieval.vector.VectorRetriever._retrieve", _must_not_retrieve)


def test_ask_factoid_label_uses_vector_arm_only(retrievable_subject, monkeypatch, stub_chat):
    home = subject_dir(retrievable_subject).parent
    _configure_chat(home)
    chat = stub_chat("Deadlocks need mutual exclusion [chunk 1].")
    monkeypatch.setattr("groundly.agents.ask.classify", lambda query, c: "factoid")
    monkeypatch.setattr("groundly.agents.ask.complete", chat)
    result = ask(
        retrievable_subject, "what causes a deadlock?", embedder=_near_embedder(), rerank=False
    )
    assert result.citations[0].chunk_id == 1

    rows = _traces(retrievable_subject)
    assert rows[-1]["arm"] == "vector"


def test_ask_multi_hop_label_fuses_graph_and_vector(retrievable_subject, monkeypatch, stub_chat):
    home = subject_dir(retrievable_subject).parent
    _configure_chat(home)
    chat = stub_chat("Deadlocks are entangled with synchronization [chunk 2].")
    monkeypatch.setattr("groundly.agents.ask.classify", lambda query, c: "multi-hop")
    monkeypatch.setattr("groundly.agents.ask.complete", chat)
    monkeypatch.setattr("groundly.agents.ask.GraphLocalRetriever", _FakeGraphLocalRetriever)
    _FakeGraphLocalRetriever.instances.clear()

    result = ask(
        retrievable_subject, "what causes a deadlock?", embedder=_near_embedder(), rerank=False
    )
    assert result.citations[0].chunk_id == 2
    assert len(_FakeGraphLocalRetriever.instances) == 1  # graph arm actually fired

    rows = _traces(retrievable_subject)
    assert rows[-1]["arm"] == "hybrid-local"


def test_ask_global_label_uses_graph_global_arm_only(retrievable_subject, monkeypatch, stub_chat):
    home = subject_dir(retrievable_subject).parent
    _configure_chat(home)
    chat = stub_chat("The course covers deadlocks broadly [chunk 2].")
    monkeypatch.setattr("groundly.agents.ask.classify", lambda query, c: "global")
    monkeypatch.setattr("groundly.agents.ask.complete", chat)
    monkeypatch.setattr("groundly.agents.ask.GraphGlobalRetriever", _FakeGraphGlobalRetriever)
    _no_vector_retrieval(monkeypatch)  # global search never touches the vector arm
    _FakeGraphGlobalRetriever.instances.clear()

    result = ask(retrievable_subject, "give me an overview of deadlocks", embedder=None)
    assert result.citations[0].chunk_id == 2
    assert len(_FakeGraphGlobalRetriever.instances) == 1

    rows = _traces(retrievable_subject)
    assert rows[-1]["arm"] == "graph-global"


def test_ask_multi_hop_degrades_to_vector_when_graph_not_built(
    retrievable_subject, monkeypatch, stub_chat
):
    home = subject_dir(retrievable_subject).parent
    _configure_chat(home)
    chat = stub_chat("Deadlocks need mutual exclusion [chunk 1].")
    monkeypatch.setattr("groundly.agents.ask.classify", lambda query, c: "multi-hop")
    monkeypatch.setattr("groundly.agents.ask.complete", chat)
    monkeypatch.setattr("groundly.agents.ask.GraphLocalRetriever", _NotBuiltRetriever)

    result = ask(
        retrievable_subject, "what causes a deadlock?", embedder=_near_embedder(), rerank=False
    )
    assert result.citations[0].chunk_id == 1

    rows = _traces(retrievable_subject)
    assert rows[-1]["arm"] == "vector"  # reflects what actually ran, not the router label
    assert rows[-1]["router_label"] == "multi-hop"


def test_ask_global_degrades_to_vector_when_graph_not_built(
    retrievable_subject, monkeypatch, stub_chat
):
    home = subject_dir(retrievable_subject).parent
    _configure_chat(home)
    chat = stub_chat("Deadlocks need mutual exclusion [chunk 1].")
    monkeypatch.setattr("groundly.agents.ask.classify", lambda query, c: "global")
    monkeypatch.setattr("groundly.agents.ask.complete", chat)
    monkeypatch.setattr("groundly.agents.ask.GraphGlobalRetriever", _NotBuiltRetriever)

    result = ask(
        retrievable_subject, "what causes a deadlock?", embedder=_near_embedder(), rerank=False
    )
    assert result.citations[0].chunk_id == 1

    rows = _traces(retrievable_subject)
    assert rows[-1]["arm"] == "vector"
    assert rows[-1]["router_label"] == "global"


def test_ask_graph_not_built_fallback_logs_info_and_still_answers(
    retrievable_subject, monkeypatch, stub_chat, caplog
):
    """The single highest-value log line in the debug-logging design: today the
    router-picked-graph-but-no-graph-built degradation is silent — `ask` must
    still return a grounded vector answer, and now also say so at INFO."""
    home = subject_dir(retrievable_subject).parent
    _configure_chat(home)
    chat = stub_chat("Deadlocks need mutual exclusion [chunk 1].")
    monkeypatch.setattr("groundly.agents.ask.classify", lambda query, c: "multi-hop")
    monkeypatch.setattr("groundly.agents.ask.complete", chat)
    monkeypatch.setattr("groundly.agents.ask.GraphLocalRetriever", _NotBuiltRetriever)

    with caplog.at_level("INFO", logger="groundly.agents.ask"):
        result = ask(
            retrievable_subject,
            "what causes a deadlock?",
            embedder=_near_embedder(),
            rerank=False,
        )

    assert result.citations[0].chunk_id == 1
    assert result.answer  # still returns a vector answer, not a refusal
    assert any(
        "degrading to vector-only" in r.message and r.levelname == "INFO" for r in caplog.records
    )
