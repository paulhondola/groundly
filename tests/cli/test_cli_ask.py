"""CLI: `ask` (enforced, cited) and `search` (raw, zero-key) verbs."""

from typer.testing import CliRunner

from groundly.core.manifest import EMBEDDING_DIM
from groundly.core.paths import subject_dir
from groundly.cli import app

runner = CliRunner()


class _NearEmbedder:
    def encode(self, texts):
        return [[1.0, 0.0] + [0.0] * (EMBEDDING_DIM - 2) for _ in texts], [{1: 1.0} for _ in texts]


def _configure_chat(subject_name):
    (subject_dir(subject_name).parent / "config.toml").write_text(
        '[providers.chat]\nbase_url = "http://x"\nmodel = "m"\n'
    )


def test_ask_prints_answer_and_sources(retrievable_subject, monkeypatch, stub_chat):
    _configure_chat(retrievable_subject)
    chat = stub_chat("Deadlocks need mutual exclusion [chunk 1].")
    monkeypatch.setattr("groundly.agents.ask.complete", chat)
    monkeypatch.setattr("groundly.llm.embeddings.BgeM3Embedder", _NearEmbedder)
    result = runner.invoke(
        app, ["ask", retrievable_subject, "what causes a deadlock?", "--no-rerank"]
    )
    assert result.exit_code == 0, result.output
    assert "mutual exclusion" in result.output
    assert "lec.pdf" in result.output
    assert "p.1" in result.output


def test_ask_refusal_exits_zero(retrievable_subject, monkeypatch, stub_chat):
    _configure_chat(retrievable_subject)
    chat = stub_chat("not covered by the course materials")
    monkeypatch.setattr("groundly.agents.ask.complete", chat)
    monkeypatch.setattr("groundly.llm.embeddings.BgeM3Embedder", _NearEmbedder)
    result = runner.invoke(
        app, ["ask", retrievable_subject, "what is the capital of France?", "--no-rerank"]
    )
    assert result.exit_code == 0, result.output
    assert "not covered by the course materials" in result.output


def test_ask_no_key_fails_while_search_succeeds(retrievable_subject, monkeypatch, stub_embedder):
    # UC-02 criterion 4: no config.toml at all
    monkeypatch.setattr("groundly.llm.embeddings.BgeM3Embedder", stub_embedder)

    ask_result = runner.invoke(
        app, ["ask", retrievable_subject, "what is a deadlock?", "--no-rerank"]
    )
    assert ask_result.exit_code == 1
    assert "[providers.chat]" in ask_result.output

    search_result = runner.invoke(app, ["search", retrievable_subject, "deadlock", "--no-rerank"])
    assert search_result.exit_code == 0, search_result.output
    assert "lec.pdf" in search_result.output


def test_search_no_rerank_plumbs_through(retrievable_subject, monkeypatch):
    captured = {}

    def fake_search(subject, query, *, k=8, rerank=True, embedder=None, reranker=None):
        captured["rerank"] = rerank
        return []

    monkeypatch.setattr("groundly.retrieval.vector.search", fake_search)
    result = runner.invoke(app, ["search", retrievable_subject, "deadlock", "--no-rerank"])
    assert result.exit_code == 0, result.output
    assert captured["rerank"] is False


def test_ask_no_rerank_plumbs_through(retrievable_subject, monkeypatch):
    captured = {}

    def fake_ask(subject, query, *, rerank=True, embedder=None, reranker=None):
        captured["rerank"] = rerank
        from groundly.agents.ask import AskResult

        return AskResult(
            answer="not covered by the course materials", citations=[], router_label=None
        )

    monkeypatch.setattr("groundly.agents.ask.ask", fake_ask)
    _configure_chat(retrievable_subject)
    result = runner.invoke(app, ["ask", retrievable_subject, "q", "--no-rerank"])
    assert result.exit_code == 0, result.output
    assert captured["rerank"] is False


def test_ask_model_download_error_fails_cleanly(retrievable_subject, monkeypatch):
    _configure_chat(retrievable_subject)
    from groundly.llm.embeddings import ModelDownloadError

    def fake_ask(*a, **k):
        raise ModelDownloadError("failed to load bge-m3: boom")

    monkeypatch.setattr("groundly.agents.ask.ask", fake_ask)
    result = runner.invoke(app, ["ask", retrievable_subject, "q", "--no-rerank"])
    assert result.exit_code == 1
    assert "failed to load bge-m3" in result.output
    assert "Traceback" not in result.output


def test_ask_chat_unreachable_error_fails_cleanly(retrievable_subject, monkeypatch):
    _configure_chat(retrievable_subject)
    from groundly.llm.chat import ChatUnreachableError

    def fake_ask(*a, **k):
        raise ChatUnreachableError("[providers.chat] at http://x is unreachable: boom")

    monkeypatch.setattr("groundly.agents.ask.ask", fake_ask)
    result = runner.invoke(app, ["ask", retrievable_subject, "q", "--no-rerank"])
    assert result.exit_code == 1
    assert "unreachable" in result.output


def test_search_model_download_error_fails_cleanly(retrievable_subject, monkeypatch):
    from groundly.llm.embeddings import ModelDownloadError

    def fake_search(*a, **k):
        raise ModelDownloadError("failed to load bge-m3: boom")

    monkeypatch.setattr("groundly.retrieval.vector.search", fake_search)
    result = runner.invoke(app, ["search", retrievable_subject, "deadlock"])
    assert result.exit_code == 1
    assert "failed to load bge-m3" in result.output


def test_ask_uninitialized_subject_fails_with_fix(tmp_path, monkeypatch):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    result = runner.invoke(app, ["ask", "NOPE", "q"])
    assert result.exit_code == 1
    assert "groundly init NOPE" in result.output
