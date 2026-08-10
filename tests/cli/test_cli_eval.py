"""`groundly eval`: the CLI wrapper over groundly/eval/runner.py. Every failure path
must print a named cause through `_fail` and exit 1 — never a bare traceback."""

import json

from typer.testing import CliRunner

from groundly.cli import app
from groundly.core.paths import subject_dir

runner = CliRunner()

_GOLD = {
    "id": "q1",
    "query": "what causes a deadlock?",
    "lang": "en",
    "class": "factoid",
    "expected": [{"file": "lec.pdf", "page": 1}],
    "source_file": "Examen.md",
}


def _gold_file(tmp_path, row=None):
    path = tmp_path / "gold.jsonl"
    path.write_text(json.dumps(row or _GOLD))
    return path


def _near_embedder():
    from groundly.core.manifest import EMBEDDING_DIM

    class E:
        def encode(self, texts):
            return [[1.0, 0.0] + [0.0] * (EMBEDDING_DIM - 2) for _ in texts], [
                {1: 1.0} for _ in texts
            ]

    return E()


def test_eval_scores_the_vector_arm_with_no_provider_configured(
    retrievable_subject, monkeypatch, tmp_path
):
    """Zero-key operation: no config.toml is written, so no provider exists. The eval
    must still produce numbers (.claude/rules/architecture.md)."""
    monkeypatch.setattr(
        "groundly.retrieval.vector.VectorRetriever.embedder",
        property(lambda self: _near_embedder()),
    )
    result = runner.invoke(
        app,
        [
            "eval",
            retrievable_subject,
            "--gold",
            str(_gold_file(tmp_path)),
            "--arms",
            "vector",
            "--no-rerank",
            "--out",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Hit rate" in result.output
    assert "vector" in result.output

    written = list((tmp_path / "out").glob("results-*.json"))
    assert len(written) == 1
    payload = json.loads(written[0].read_text())
    assert payload["subject"] == retrievable_subject
    assert payload["questions"] == 1
    assert payload["by_arm"][0]["slice"]["arm"] == "vector"


def test_eval_unknown_arm_named_before_any_work(retrievable_subject, tmp_path):
    result = runner.invoke(
        app,
        ["eval", retrievable_subject, "--gold", str(_gold_file(tmp_path)), "--arms", "graph-locul"],
    )
    assert result.exit_code == 1
    # Wording comes from `retrieval.arms.validate_arms`, shared with `eval.runner.run`
    # so the CLI and the library cannot describe the same mistake differently.
    assert "unknown retrieval arm(s): graph-locul" in result.output


def test_eval_unimplemented_arm_is_not_reported_as_a_typo(retrievable_subject, tmp_path):
    """Arm 4 is in `ARM_TABLE` with no builder. The CLI used to screen against `ARMS`
    and call it "unknown", which sends someone hunting a typo they did not make —
    `retrieve_for_arm`'s distinguishing message was unreachable from the one surface
    that accepts `--arms`."""
    result = runner.invoke(
        app,
        ["eval", retrievable_subject, "--gold", str(_gold_file(tmp_path)), "--arms", "adaptive"],
    )
    assert result.exit_code == 1
    assert "declared but not implemented" in result.output
    assert "unknown" not in result.output


def test_eval_bad_gold_set_fails_with_a_named_cause(retrievable_subject, tmp_path):
    bad = _GOLD | {"expected": [{"file": "Examen.md"}]}  # labels its own source file
    result = runner.invoke(
        app,
        ["eval", retrievable_subject, "--gold", str(_gold_file(tmp_path, bad)), "--arms", "vector"],
    )
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "a question source for this gold set" in result.output


def test_eval_missing_gold_set_fails_with_a_named_cause(retrievable_subject, tmp_path):
    result = runner.invoke(
        app,
        ["eval", retrievable_subject, "--gold", str(tmp_path / "absent.jsonl"), "--arms", "vector"],
    )
    assert result.exit_code == 1
    assert "no gold set at" in result.output


def test_eval_graph_arm_without_a_graph_fails_rather_than_reporting_baseline_numbers(
    retrievable_subject, monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "groundly.retrieval.vector.VectorRetriever.embedder",
        property(lambda self: _near_embedder()),
    )
    result = runner.invoke(
        app,
        [
            "eval",
            retrievable_subject,
            "--gold",
            str(_gold_file(tmp_path)),
            "--arms",
            "graph-global",
            "--no-rerank",
        ],
    )
    assert result.exit_code == 1
    assert "degraded to 'vector'" in result.output


def test_eval_uninitialized_subject_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    result = runner.invoke(app, ["eval", "NOPE"])
    assert result.exit_code == 1
    assert "not initialized" in result.output


def test_eval_defaults_the_gold_path_to_the_subject_convention(retrievable_subject, tmp_path):
    """No --gold: the verb looks under ./evals/<SUBJECT>/gold.jsonl and says so when
    it isn't there."""
    assert subject_dir(retrievable_subject).exists()
    result = runner.invoke(app, ["eval", retrievable_subject], catch_exceptions=False)
    assert result.exit_code == 1
    assert f"evals/{retrievable_subject}/gold.jsonl" in result.output


def test_cli_renders_a_dash_and_explains_it_for_an_unranked_arm(
    retrievable_subject, monkeypatch, tmp_path
):
    """graph-global has no relevance order, so its MRR cell must read '—' with a reason —
    a number there would be read as ranking evidence. Forced onto the vector arm so the
    rendering path is exercised without a built graph or a provider."""
    monkeypatch.setattr(
        "groundly.retrieval.vector.VectorRetriever.embedder",
        property(lambda self: _near_embedder()),
    )
    monkeypatch.setattr("groundly.eval.runner.UNRANKED_ARMS", frozenset({"vector"}))

    result = runner.invoke(
        app,
        [
            "eval",
            retrievable_subject,
            "--gold",
            str(_gold_file(tmp_path)),
            "--arms",
            "vector",
            "--no-rerank",
            "--out",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "—" in result.output
    assert "not relevance order" in result.output

    payload = json.loads(next((tmp_path / "out").glob("results-*.json")).read_text())
    assert payload["by_arm"][0]["mrr"] is None
    assert payload["rows"][0]["reciprocal_rank"] is None


def test_cli_still_reports_mrr_for_ranked_arms(retrievable_subject, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "groundly.retrieval.vector.VectorRetriever.embedder",
        property(lambda self: _near_embedder()),
    )
    result = runner.invoke(
        app,
        [
            "eval",
            retrievable_subject,
            "--gold",
            str(_gold_file(tmp_path)),
            "--arms",
            "vector",
            "--no-rerank",
            "--out",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "not relevance order" not in result.output
    payload = json.loads(next((tmp_path / "out").glob("results-*.json")).read_text())
    assert payload["by_arm"][0]["mrr"] is not None
