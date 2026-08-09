"""groundly/agents/ask.py: retrieval -> assemble -> chat -> citation resolution ->
trace row, for every outcome (UC-02), plus the decision-28 boundary — the product
path selects `vector` and cannot reach a graph arm, while `retrieve_for_arm` still
serves all three to `groundly eval`."""

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


def _configure_chat_and_router(home):
    """Both call classes configured. Needed to prove `ask()` skips the router for the
    right reason: with no `[providers.router]` section `classify()` returns None on its
    own, so a null label would prove nothing."""
    (home / "config.toml").write_text(
        '[providers.chat]\nbase_url = "http://x"\nmodel = "m"\napi_key = "sk"\n'
        '\n[providers.router]\nbase_url = "http://x"\nmodel = "m"\napi_key = "sk"\n'
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


def test_ask_never_classifies_and_always_reports_a_null_router_label(
    retrievable_subject, monkeypatch, stub_chat
):
    """Decision 28: `ask()` does not run the router. `router_label` keeps meaning "what
    the router said" — it is `None` here because nothing asked, not because the router
    was unconfigured — so a re-admitted router needs no schema change.

    A configured router provider is deliberately present: without it this would pass for
    the old reason (`classify` short-circuits on no provider) rather than the new one."""
    home = subject_dir(retrievable_subject).parent
    _configure_chat_and_router(home)

    def _must_not_classify(*a, **kw):
        raise AssertionError("ask() must not consult the router — decision 28")

    monkeypatch.setattr("groundly.agents.router.classify", _must_not_classify)
    chat = stub_chat("A deadlock needs mutual exclusion [chunk 1].")
    monkeypatch.setattr("groundly.agents.ask.complete", chat)

    result = ask(
        retrievable_subject, "what causes a deadlock?", embedder=_near_embedder(), rerank=False
    )
    assert result.router_label is None
    assert len(chat.calls) == 1  # generation only — the router round-trip is gone

    rows = _traces(retrievable_subject)
    assert rows[-1]["router_label"] is None


# --- the product path selects exactly one arm (decision 28) --------------------


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
    """Stubs `GraphGlobalRetriever` at arms.py's import site."""

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
    """Stubs either graph retriever to simulate a subject with no graph built yet.

    `instances` exists because the *real* retriever also raises `GraphNotBuiltError` on
    a graph-less fixture: without recording construction, a degradation test passes
    identically whether the patch took effect or not."""

    instances: list["_NotBuiltRetriever"] = []

    def __init__(self, subject):
        self.subject = subject
        _NotBuiltRetriever.instances.append(self)

    def retrieve(self, query):
        raise GraphNotBuiltError()


def _no_vector_retrieval(monkeypatch):
    """Fails the test loudly if the vector arm is ever asked to retrieve — used to
    assert graph-only arms don't fall back to vector unless degrading."""

    def _must_not_retrieve(self, query):
        raise AssertionError("vector retriever must not run for this arm")

    monkeypatch.setattr("groundly.retrieval.vector.VectorRetriever._retrieve", _must_not_retrieve)


def test_ask_uses_the_vector_arm(retrievable_subject, monkeypatch, stub_chat):
    home = subject_dir(retrievable_subject).parent
    _configure_chat(home)
    chat = stub_chat("Deadlocks need mutual exclusion [chunk 1].")
    monkeypatch.setattr("groundly.agents.ask.complete", chat)
    result = ask(
        retrievable_subject, "what causes a deadlock?", embedder=_near_embedder(), rerank=False
    )
    assert result.citations[0].chunk_id == 1

    rows = _traces(retrievable_subject)
    assert rows[-1]["arm"] == "vector"


def test_ask_cannot_reach_a_graph_arm(retrievable_subject, monkeypatch, stub_chat):
    """The retirement has to be structural, not a default. Both graph retrievers are
    replaced by classes that fail on construction, so any surviving route from a user
    question into graphrag fails this test rather than quietly costing a provider call.

    Constructing is the tripwire, not retrieving: `hybrid-local` builds the retriever
    before it fuses, so a route that dies later would still have paid for it."""
    home = subject_dir(retrievable_subject).parent
    _configure_chat_and_router(home)

    class _Forbidden:
        def __init__(self, *a, **kw):
            raise AssertionError("ask() reached a retired graph arm — decision 28")

    monkeypatch.setattr("groundly.retrieval.graph.GraphLocalRetriever", _Forbidden)
    monkeypatch.setattr("groundly.retrieval.arms.GraphGlobalRetriever", _Forbidden)
    chat = stub_chat("Deadlocks need mutual exclusion [chunk 1].")
    monkeypatch.setattr("groundly.agents.ask.complete", chat)

    for query in ("what causes a deadlock?", "give me an overview", "how do X and Y relate?"):
        result = ask(retrievable_subject, query, embedder=_near_embedder(), rerank=False)
        assert result.citations[0].chunk_id == 1
    assert {row["arm"] for row in _traces(retrievable_subject)} == {"vector"}


def test_ask_takes_no_arm_parameter(retrievable_subject):
    """While `arm=` existed, the product could still be pointed at a retired arm. Asserted
    on the signature rather than by calling with it, so this fails at the moment someone
    re-adds the parameter rather than only when something passes a graph arm to it."""
    import inspect

    assert "arm" not in inspect.signature(ask).parameters


def test_product_arms_is_a_strict_subset_of_arms():
    """`ARMS` is what the eval may score; `PRODUCT_ARMS` is what a user question may
    reach. Strict, because the day they are equal the retirement has been undone."""
    from groundly.retrieval.arms import ARMS, PRODUCT_ARMS

    assert set(PRODUCT_ARMS) < set(ARMS)
    assert set(PRODUCT_ARMS) == {"vector"}


def test_retrieve_for_arm_graph_not_built_fallback_logs_info(
    retrievable_subject, monkeypatch, caplog
):
    """The degradation moved with the arms: no product path reaches it any more, but the
    eval still runs graph arms against subjects that may have no graph, and that must be
    visible at INFO rather than silently scoring as the baseline."""
    from groundly.retrieval.arms import retrieve_for_arm
    from groundly.core.store import SubjectStore

    monkeypatch.setattr("groundly.retrieval.graph.GraphLocalRetriever", _NotBuiltRetriever)
    _NotBuiltRetriever.instances.clear()
    store = SubjectStore(subject_dir(retrievable_subject) / "store.db")

    with caplog.at_level("INFO", logger="groundly.retrieval.arms"):
        nodes, _path, arm = retrieve_for_arm(
            retrievable_subject,
            "what causes a deadlock?",
            "hybrid-local",
            store=store,
            embedder=_near_embedder(),
            rerank=False,
        )

    assert arm == "vector"
    assert nodes
    assert _NotBuiltRetriever.instances, "the stubbed graph arm was never reached"
    assert any(
        "degrading to vector-only" in r.message and r.levelname == "INFO" for r in caplog.records
    )


# --- explicit arm override (the eval harness's entry point) --------------------


def test_retrieve_for_arm_rejects_an_unknown_arm(retrievable_subject):
    """A typo in `groundly eval --arms` must fail loudly, not silently score the
    baseline under another arm's name."""
    from groundly.retrieval.arms import retrieve_for_arm
    from groundly.core.store import SubjectStore

    store = SubjectStore(subject_dir(retrievable_subject) / "store.db")
    with pytest.raises(ValueError, match="unknown retrieval arm 'graph-locul'"):
        retrieve_for_arm(retrievable_subject, "q", "graph-locul", store=store)


def test_retrieve_for_arm_needs_no_chat_provider(retrievable_subject, monkeypatch):
    """Zero-key retrieval is what makes the eval runnable offline — no provider is
    configured in this test and none is required."""
    from groundly.retrieval.arms import retrieve_for_arm
    from groundly.core.store import SubjectStore

    def _explode(*a, **k):
        raise AssertionError("retrieval must not call an LLM")

    monkeypatch.setattr("groundly.agents.ask.complete", _explode)
    store = SubjectStore(subject_dir(retrievable_subject) / "store.db")
    nodes, path, arm = retrieve_for_arm(
        retrievable_subject,
        "deadlock",
        "vector",
        store=store,
        embedder=_near_embedder(),
        rerank=False,
    )
    assert arm == "vector"
    assert [n.node.metadata["chunk_id"] for n in nodes]
    assert path


def test_retrieve_for_arm_reports_degradation_in_arm_actual(retrievable_subject, monkeypatch):
    from groundly.retrieval.arms import retrieve_for_arm
    from groundly.core.store import SubjectStore

    monkeypatch.setattr("groundly.retrieval.arms.GraphGlobalRetriever", _NotBuiltRetriever)
    _NotBuiltRetriever.instances.clear()
    store = SubjectStore(subject_dir(retrievable_subject) / "store.db")
    _nodes, _path, arm = retrieve_for_arm(
        retrievable_subject,
        "deadlock",
        "graph-global",
        store=store,
        embedder=_near_embedder(),
        rerank=False,
    )
    assert arm == "vector"  # caller sees the degradation; the eval treats it as fatal
    assert _NotBuiltRetriever.instances, "the stubbed graph arm was never reached"


def test_retrieve_for_arm_runs_the_graph_global_arm_for_the_eval(retrievable_subject, monkeypatch):
    """The arms are retired from the product path, not deleted — `groundly eval --arms`
    is what keeps the negative result reproducible from shipped code, so the eval's entry
    point must still reach graphrag."""
    from groundly.retrieval.arms import retrieve_for_arm
    from groundly.core.store import SubjectStore

    monkeypatch.setattr("groundly.retrieval.arms.GraphGlobalRetriever", _FakeGraphGlobalRetriever)
    _FakeGraphGlobalRetriever.instances.clear()
    _no_vector_retrieval(monkeypatch)  # global search never touches the vector arm
    store = SubjectStore(subject_dir(retrievable_subject) / "store.db")

    nodes, _path, arm = retrieve_for_arm(
        retrievable_subject, "give me an overview", "graph-global", store=store
    )

    assert arm == "graph-global"
    assert len(_FakeGraphGlobalRetriever.instances) == 1
    assert [n.node.metadata["chunk_id"] for n in nodes] == [2]


def test_ask_truncates_candidates_to_context_k(retrievable_subject, monkeypatch, stub_chat):
    """`retrieve_for_arm` returns each arm's full candidate list so the eval can score
    every k from one sweep; applying `context_k` is `ask()`'s job. Before this, the
    `global` router label assembled 1,138 chunks into a 16,384-token window.

    The cap must land *before* `chunk_ids`, or `resolve_citations` would accept a citation
    to a chunk the model was never shown."""
    from groundly.core.config import load_settings

    home = subject_dir(retrievable_subject).parent
    _configure_chat(home)
    context_k = load_settings().retrieval.context_k

    wide = [
        NodeWithScore(
            node=TextNode(
                text=f"text {i}",
                id_=str(i),
                metadata={"chunk_id": i, "filename": "f.md", "page": None, "heading_path": ""},
            ),
            score=1.0 / (i + 1),
        )
        for i in range(context_k + 40)
    ]
    monkeypatch.setattr(
        "groundly.agents.ask.retrieve_for_arm",
        lambda *a, **kw: (wide, ["stub"], "vector"),
    )
    chat = stub_chat("Grounded [chunk 0].")
    monkeypatch.setattr("groundly.agents.ask.complete", chat)

    ask(retrievable_subject, "anything?", embedder=_near_embedder(), rerank=False)

    rows = _traces(retrievable_subject)
    assert len(json.loads(rows[-1]["chunk_ids"])) == context_k
    # a chunk past the cap must not be citable, even though retrieval returned it
    assert json.loads(rows[-1]["chunk_ids"]) == list(range(context_k))
