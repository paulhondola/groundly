"""groundly/agents/ask.py: retrieval -> assemble -> chat -> citation resolution ->
trace row, for every outcome (UC-02), plus the arm-selection boundary — `ask` runs the
arm it is given and defaults to `vector`, refuses the unranked arm, and refuses a graph
arm on a subject with no graph instead of quietly answering as the baseline."""

import json

import pytest
from llama_index.core.schema import NodeWithScore, TextNode

from groundly.agents.ask import NoCitationsError, ask
from groundly.core.paths import subject_dir
from groundly.core.progress import connect_progress
from groundly.core.subject import Subject
from groundly.llm.config import ProviderNotConfiguredError
from groundly.retrieval.graph import GraphNotBuiltError


def _record_graph(subject):
    """Make `Subject.graph_is_built()` true the way a real build does — the directory
    *and* the manifest hash, since either alone is the partial-build state the predicate
    exists to reject."""
    subj = Subject(subject)
    (subj.root_dir / "graph").mkdir(exist_ok=True)
    manifest = subj.load_manifest()
    manifest.graphrag.corpus_hash = "deadbeef"
    subj.save_manifest(manifest)


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
    """Stubs `GraphLocalRetriever` at its defining module — always returns chunk 2,
    never runs real graphrag. Patched on `retrieval/graph.py` rather than on a
    re-import, because `HybridLocalRetriever` imports it inside `_retrieve` (to keep the
    zero-key search path clear of graphrag) and so resolves it at call time."""

    instances: list["_FakeGraphLocalRetriever"] = []

    def __init__(self, subject):
        self.subject = subject
        self.path: list[str] = []
        _FakeGraphLocalRetriever.instances.append(self)

    def retrieve(self, query):
        self.path = ["graphrag-local", "entity-search"]
        return [_graph_node(2)]


class _FakeHybridLocalRetriever:
    """Stubs `HybridLocalRetriever` at arms.py's import site — returns chunk 1 so the
    citation resolves exactly as the vector arm's does, leaving the arm label as the
    only difference the assertions can see."""

    def __init__(self, store, subject, **kw):
        self.subject = subject
        self.path: list[str] = []

    def retrieve(self, query):
        self.path = ["vector", "graphrag-local", "rrf"]
        return [_graph_node(1)]


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
    a graph-less fixture: without recording construction, the test passes identically
    whether the patch took effect or not."""

    instances: list["_NotBuiltRetriever"] = []

    def __init__(self, subject):
        self.subject = subject
        _NotBuiltRetriever.instances.append(self)

    def retrieve(self, query):
        raise GraphNotBuiltError()


def _no_vector_retrieval(monkeypatch):
    """Fails the test loudly if the vector arm is ever asked to retrieve — used to
    assert a graph arm runs as itself and never as the baseline."""

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


def test_ask_reaches_the_arm_it_is_given(retrievable_subject, monkeypatch, stub_chat):
    """The whole point of the change: `arm=` routes, and the trace records the arm that
    actually ran. `hybrid-local` needs the graph recorded or the preflight refuses it
    before the stub is ever built."""
    home = subject_dir(retrievable_subject).parent
    _configure_chat_and_router(home)
    _record_graph(retrievable_subject)
    monkeypatch.setattr("groundly.retrieval.arms.HybridLocalRetriever", _FakeHybridLocalRetriever)
    chat = stub_chat("Deadlocks need mutual exclusion [chunk 1].")
    monkeypatch.setattr("groundly.agents.ask.complete", chat)

    for arm in ("vector", "hybrid-local"):
        result = ask(
            retrievable_subject,
            "what causes a deadlock?",
            arm=arm,
            embedder=_near_embedder(),
            rerank=False,
        )
        assert result.citations[0].chunk_id == 1
        assert _traces(retrievable_subject)[-1]["arm"] == arm


def test_ask_defaults_to_vector(retrievable_subject, monkeypatch, stub_chat):
    """The measured winner stays the default. Asserted on the signature *and* on a call
    with no `arm=`, so neither a changed default nor a call path that overrides it
    slips through."""
    import inspect

    from groundly.retrieval.arms import VECTOR

    assert inspect.signature(ask).parameters["arm"].default == VECTOR

    home = subject_dir(retrievable_subject).parent
    _configure_chat(home)
    monkeypatch.setattr(
        "groundly.agents.ask.complete", stub_chat("Deadlocks need mutual exclusion [chunk 1].")
    )
    ask(retrievable_subject, "what causes a deadlock?", embedder=_near_embedder(), rerank=False)
    assert _traces(retrievable_subject)[-1]["arm"] == VECTOR


def test_ask_refuses_an_unranked_arm(retrievable_subject, monkeypatch):
    """`graph-global` emits `sorted(chunk_ids)` — ascending rowid, no relevance order —
    so `ask`'s context_k truncation would hand the model whichever chunks sort first, the
    same ones for every question. It stays scoreable by the eval, which only computes
    order-insensitive metrics over it."""
    home = subject_dir(retrievable_subject).parent
    _configure_chat(home)
    _record_graph(retrievable_subject)

    def _explode(*a, **k):
        raise AssertionError("an unranked arm must be refused before anything runs")

    monkeypatch.setattr("groundly.agents.ask.complete", _explode)
    monkeypatch.setattr("groundly.retrieval.arms.GraphGlobalRetriever", _explode)

    with pytest.raises(ValueError, match="no relevance order"):
        ask(retrievable_subject, "give me an overview", arm="graph-global")
    assert not _traces(retrievable_subject)


def test_a_graph_arm_fails_loudly_without_a_graph(retrievable_subject, monkeypatch):
    """No silent degradation to vector. The other two assertions are the real point: the
    preflight lands before `TracedAnswer` opens and before any model loads, so a run that
    cannot work leaves no trace row and costs nothing."""
    home = subject_dir(retrievable_subject).parent
    _configure_chat(home)

    def _explode(*a, **k):
        raise AssertionError("nothing may run once the graph preflight has failed")

    monkeypatch.setattr("groundly.agents.ask.complete", _explode)
    monkeypatch.setattr("groundly.retrieval.arms.HybridLocalRetriever", _explode)

    with pytest.raises(GraphNotBuiltError):
        ask(retrievable_subject, "what causes a deadlock?", arm="hybrid-local")
    assert not _traces(retrievable_subject)


def test_ask_refuses_a_graph_arm_on_a_partial_build(retrievable_subject, monkeypatch):
    """A refused or interrupted build leaves `graph/` behind on purpose so the retry
    keeps graphrag's paid-for cache. The directory alone is not a graph, and `ask` must
    not treat it as one."""
    home = subject_dir(retrievable_subject).parent
    _configure_chat(home)
    (Subject(retrievable_subject).root_dir / "graph").mkdir(exist_ok=True)  # no corpus_hash

    with pytest.raises(GraphNotBuiltError):
        ask(retrievable_subject, "what causes a deadlock?", arm="hybrid-local")


def test_retrieve_for_arm_propagates_graph_not_built(retrievable_subject, monkeypatch):
    """The backstop under both preflights. It used to catch this and return the vector
    arm's nodes under the graph arm's name; now it propagates, so no caller can report
    baseline numbers as a graph arm's."""
    from groundly.core.store import SubjectStore
    from groundly.retrieval.arms import retrieve_for_arm

    monkeypatch.setattr("groundly.retrieval.graph.GraphLocalRetriever", _NotBuiltRetriever)
    _NotBuiltRetriever.instances.clear()
    store = SubjectStore(subject_dir(retrievable_subject) / "store.db")

    with pytest.raises(GraphNotBuiltError):
        retrieve_for_arm(
            retrievable_subject,
            "what causes a deadlock?",
            "hybrid-local",
            store=store,
            embedder=_near_embedder(),
            rerank=False,
        )
    assert _NotBuiltRetriever.instances, "the stubbed graph arm was never reached"


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
    nodes, path = retrieve_for_arm(
        retrievable_subject,
        "deadlock",
        "vector",
        store=store,
        embedder=_near_embedder(),
        rerank=False,
    )
    assert [n.node.metadata["chunk_id"] for n in nodes]
    assert path


def test_retrieve_for_arm_runs_the_graph_global_arm_for_the_eval(retrievable_subject, monkeypatch):
    """`graph-global` is off the *ask* path because its output carries no relevance
    order, not because it was deleted — `groundly eval --arms` keeps the negative result
    reproducible from shipped code, so the eval's entry point must still reach graphrag."""
    from groundly.core.store import SubjectStore
    from groundly.retrieval.arms import retrieve_for_arm

    monkeypatch.setattr("groundly.retrieval.arms.GraphGlobalRetriever", _FakeGraphGlobalRetriever)
    _FakeGraphGlobalRetriever.instances.clear()
    _no_vector_retrieval(monkeypatch)  # global search never touches the vector arm
    store = SubjectStore(subject_dir(retrievable_subject) / "store.db")

    nodes, _path = retrieve_for_arm(
        retrievable_subject, "give me an overview", "graph-global", store=store
    )

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
        lambda *a, **kw: (wide, ["stub"]),
    )
    chat = stub_chat("Grounded [chunk 0].")
    monkeypatch.setattr("groundly.agents.ask.complete", chat)

    ask(retrievable_subject, "anything?", embedder=_near_embedder(), rerank=False)

    rows = _traces(retrievable_subject)
    assert len(json.loads(rows[-1]["chunk_ids"])) == context_k
    # a chunk past the cap must not be citable, even though retrieval returned it
    assert json.loads(rows[-1]["chunk_ids"]) == list(range(context_k))
