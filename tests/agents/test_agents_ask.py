"""groundly/agents/ask.py: router -> retrieval -> assemble -> chat -> citation
resolution -> trace row, for every outcome (UC-02)."""

import json

import pytest

from groundly.agents.ask import NoCitationsError, ask
from groundly.core.paths import subject_dir
from groundly.core.store import connect_progress
from groundly.llm.config import ProviderNotConfiguredError


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
