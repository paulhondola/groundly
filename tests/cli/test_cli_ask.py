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


def _capturing_ask(captured):
    def fake_ask(subject, query, *, arm="vector", rerank=True, embedder=None, reranker=None):
        captured.update(arm=arm, rerank=rerank)
        from groundly.agents.ask import AskResult

        return AskResult(
            answer="not covered by the course materials", citations=[], router_label=None
        )

    return fake_ask


def test_ask_no_rerank_plumbs_through(retrievable_subject, monkeypatch):
    captured = {}
    monkeypatch.setattr("groundly.agents.ask.ask", _capturing_ask(captured))
    _configure_chat(retrievable_subject)
    result = runner.invoke(app, ["ask", retrievable_subject, "q", "--no-rerank"])
    assert result.exit_code == 0, result.output
    assert captured["rerank"] is False


def test_the_cli_default_arm_matches_the_table():
    """`cli/ask.py` spells the default as a literal rather than importing
    `arms.VECTOR`, because a signature default is evaluated at import time and the arm
    table costs ~6.4s of graphrag and pandas that every `groundly --help` would pay.
    That trade buys a drift risk, so it gets the one assertion that closes it."""
    from groundly.cli.ask import _DEFAULT_ARM
    from groundly.retrieval.arms import ARM_TABLE, UNRANKED_ARMS, VECTOR

    assert _DEFAULT_ARM == VECTOR
    assert _DEFAULT_ARM in ARM_TABLE and _DEFAULT_ARM not in UNRANKED_ARMS


def test_the_arm_help_text_names_every_askable_arm():
    """The `--arm` help lists the askable arms as prose, which is the other half of the
    same shortcut. Admitting a fourth arm must not leave the help text describing three."""
    from groundly.retrieval.arms import ARMS, UNRANKED_ARMS

    help_text = runner.invoke(app, ["ask", "--help"]).output
    for arm in (n for n in ARMS if n not in UNRANKED_ARMS):
        assert arm in help_text, f"--arm help does not mention the askable arm {arm!r}"


def test_ask_arm_plumbs_through_and_defaults_to_vector(retrievable_subject, monkeypatch):
    """`--arm`, singular, beside `groundly eval --arms`, plural — different surfaces,
    unambiguous names."""
    captured = {}
    monkeypatch.setattr("groundly.agents.ask.ask", _capturing_ask(captured))
    _configure_chat(retrievable_subject)

    assert runner.invoke(app, ["ask", retrievable_subject, "q"]).exit_code == 0
    assert captured["arm"] == "vector"

    result = runner.invoke(app, ["ask", retrievable_subject, "q", "--arm", "hybrid-local"])
    assert result.exit_code == 0, result.output
    assert captured["arm"] == "hybrid-local"


def test_ask_names_which_arm_mistake_it_was(retrievable_subject, monkeypatch):
    """Three refusals, three causes. The unranked one is the easiest to mistake for a
    typo, so it has to say why `graph-global` is scoreable but not askable."""

    def _unreachable(*a, **kw):
        raise AssertionError("the CLI must screen the arm before calling ask()")

    monkeypatch.setattr("groundly.agents.ask.ask", _unreachable)
    _configure_chat(retrievable_subject)

    for arm, expected in (
        ("vektor", "unknown retrieval arm"),
        ("adaptive", "declared but not implemented"),
        ("graph-global", "no relevance order"),
    ):
        result = runner.invoke(app, ["ask", retrievable_subject, "q", "--arm", arm])
        assert result.exit_code != 0, arm
        assert expected in result.output, f"{arm}: {result.output}"


def test_ask_graph_arm_without_a_graph_prints_a_cause_not_a_traceback(
    retrievable_subject, monkeypatch
):
    """`GraphNotBuiltError` joins the except tuple, so the student is told what to build
    rather than shown a stack trace (conventions: name the cause specifically)."""
    _configure_chat(retrievable_subject)
    result = runner.invoke(app, ["ask", retrievable_subject, "q", "--arm", "hybrid-local"])

    assert result.exit_code != 0
    assert "graph not built" in result.output
    # Names the arm: the default one works on this subject, so "no graph" alone does not
    # explain why this invocation is the one that failed.
    assert "'hybrid-local' arm needs one" in result.output
    assert "Traceback" not in result.output


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
