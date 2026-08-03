"""groundly/eval/runner.py: the eval loop and its results document."""

import json

import pytest
from llama_index.core.schema import NodeWithScore, TextNode

from groundly.eval.runner import ArmDegradedError, run, write_results

_GOLD = [
    {
        "id": "q1",
        "query": "race condition?",
        "lang": "en",
        "class": "factoid",
        "expected": [{"file": "lec.pdf", "page": 7}],
        "source_file": "Examen.md",
    },
    {
        "id": "q2",
        "query": "ce este o barieră?",
        "lang": "ro",
        "class": "multi-hop",
        "expected": [{"file": "lec.pdf", "page": 8}],
        "source_file": None,
    },
]


class StubStore:
    def all_chunks(self):
        return [
            {"chunk_id": 1, "filename": "lec.pdf", "page": 7},
            {"chunk_id": 2, "filename": "lec.pdf", "page": 8},
            {"chunk_id": 9, "filename": "Examen.md", "page": None},
        ]


def _nodes(*chunk_ids):
    return [
        NodeWithScore(node=TextNode(text="x", metadata={"chunk_id": cid}), score=1.0)
        for cid in chunk_ids
    ]


@pytest.fixture
def gold_file(tmp_path):
    path = tmp_path / "gold.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in _GOLD))
    return path


def _stub_retrieve(monkeypatch, by_arm, degrade_to=None):
    def _retrieve(subject, query, arm, **kwargs):
        return _nodes(*by_arm[arm]), ["stub"], degrade_to or arm

    monkeypatch.setattr("groundly.eval.runner.retrieve_for_arm", _retrieve)


def test_run_scores_every_question_against_every_arm(gold_file, monkeypatch):
    _stub_retrieve(monkeypatch, {"vector": [1], "graph-global": [2]})
    results = run("TEST", gold_file, StubStore(), arms=["vector", "graph-global"])

    assert results["questions"] == 2
    assert len(results["rows"]) == 4  # 2 questions x 2 arms
    per_arm = {a["slice"]["arm"]: a for a in results["by_arm"]}
    # vector always returns chunk 1 (= q1's label), graph-global always chunk 2 (= q2's)
    assert per_arm["vector"]["hit_rate"] == 0.5
    assert per_arm["graph-global"]["hit_rate"] == 0.5


def test_run_slices_by_class_and_language(gold_file, monkeypatch):
    _stub_retrieve(monkeypatch, {"vector": [1]})
    results = run("TEST", gold_file, StubStore(), arms=["vector"])

    by_class = {a["slice"]["klass"]: a for a in results["by_arm_class"]}
    assert by_class["factoid"]["hit_rate"] == 1.0
    assert by_class["multi-hop"]["hit_rate"] == 0.0
    by_lang = {a["slice"]["lang"]: a for a in results["by_arm_lang"]}
    assert set(by_lang) == {"en", "ro"}


def test_run_measures_leakage_from_the_question_source_file(gold_file, monkeypatch):
    """Chunk 9 is Examen.md — q1's own source. Retrieving it is leakage, not a hit."""
    _stub_retrieve(monkeypatch, {"vector": [9]})
    results = run("TEST", gold_file, StubStore(), arms=["vector"])

    rows = {r["question_id"]: r for r in results["rows"]}
    assert rows["q1"]["leakage"] == 1.0
    assert rows["q1"]["hit"] is False
    assert rows["q2"]["leakage"] == 0.0  # q2 has no source_file


def test_run_refuses_when_a_graph_arm_degraded_to_vector(gold_file, monkeypatch):
    """Reporting baseline numbers under a graph arm's name is worse than no numbers."""
    _stub_retrieve(monkeypatch, {"graph-global": [1]}, degrade_to="vector")
    with pytest.raises(ArmDegradedError, match="degraded to 'vector'"):
        run("TEST", gold_file, StubStore(), arms=["graph-global"])


def test_run_reports_progress_per_question_arm(gold_file, monkeypatch):
    _stub_retrieve(monkeypatch, {"vector": [1]})
    seen = []
    run(
        "TEST",
        gold_file,
        StubStore(),
        arms=["vector"],
        on_question=lambda q, a: seen.append((q.id, a)),
    )
    assert seen == [("q1", "vector"), ("q2", "vector")]


def test_run_carries_resolution_warnings_into_the_results(gold_file, monkeypatch):
    class EmptyStore:
        def all_chunks(self):
            return []

    _stub_retrieve(monkeypatch, {"vector": []})
    results = run("TEST", gold_file, EmptyStore(), arms=["vector"])
    assert len(results["warnings"]) == 2
    assert "matches no chunk" in results["warnings"][0]


def test_write_results_lands_a_timestamped_json(gold_file, monkeypatch, tmp_path):
    _stub_retrieve(monkeypatch, {"vector": [1]})
    results = run("TEST", gold_file, StubStore(), arms=["vector"])
    path = write_results(results, tmp_path / "out")

    assert path.parent.name == "out"
    assert path.name.startswith("results-") and path.suffix == ".json"
    assert json.loads(path.read_text())["subject"] == "TEST"


def test_run_records_a_failed_question_and_carries_on(gold_file, monkeypatch):
    """One question must not destroy the run — a 48-question two-graph-arm sweep is
    hours long, and the first real run died on a single context overflow."""
    calls = []

    def _retrieve(subject, query, arm, **kwargs):
        calls.append(query)
        if len(calls) == 1:
            raise RuntimeError("exceeds the available context size (8192 tokens)")
        return _nodes(1), ["stub"], arm

    monkeypatch.setattr("groundly.eval.runner.retrieve_for_arm", _retrieve)
    results = run("TEST", gold_file, StubStore(), arms=["vector"])

    assert len(calls) == 2  # the second question still ran
    assert results["errors"] == 1
    rows = {r["question_id"]: r for r in results["rows"]}
    assert "8192 tokens" in rows["q1"]["error"]
    assert rows["q2"]["error"] is None


def test_errored_questions_are_excluded_from_quality_metrics(gold_file, monkeypatch):
    """An outage must not read as an arm retrieving badly. q1 errors, q2 hits — hit rate
    is 100% of what ran, not 50% of what was attempted."""

    def _retrieve(subject, query, arm, **kwargs):
        if "deadlock" in query or "race" in query:
            raise RuntimeError("provider unreachable")
        return _nodes(2), ["stub"], arm

    monkeypatch.setattr("groundly.eval.runner.retrieve_for_arm", _retrieve)
    results = run("TEST", gold_file, StubStore(), arms=["vector"])

    agg = results["by_arm"][0]
    assert agg["errors"] == 1
    assert agg["n"] == 1  # only the question that ran
    assert agg["hit_rate"] == 1.0


def test_a_total_provider_outage_scores_nothing_rather_than_zero(gold_file, monkeypatch):
    """Every question failing must be visible as errors, not as a real 0% hit rate."""

    def _retrieve(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("groundly.eval.runner.retrieve_for_arm", _retrieve)
    results = run("TEST", gold_file, StubStore(), arms=["vector"])

    agg = results["by_arm"][0]
    assert (agg["n"], agg["errors"]) == (0, 2)
    assert results["errors"] == 2


def test_interrupt_keeps_what_ran_and_marks_it_partial(gold_file, monkeypatch):
    """Hours of graph queries are worth keeping — but a half-run indistinguishable from
    a full one is worse than losing it."""

    def _retrieve(subject, query, arm, **kwargs):
        if "barieră" in query:
            raise KeyboardInterrupt
        return _nodes(1), ["stub"], arm

    monkeypatch.setattr("groundly.eval.runner.retrieve_for_arm", _retrieve)
    results = run("TEST", gold_file, StubStore(), arms=["vector"])

    assert results["partial"] is True
    assert len(results["rows"]) == 1  # q1 kept, q2 never completed
    assert results["rows"][0]["question_id"] == "q1"


def test_a_complete_run_is_not_marked_partial(gold_file, monkeypatch):
    _stub_retrieve(monkeypatch, {"vector": [1]})
    assert run("TEST", gold_file, StubStore(), arms=["vector"])["partial"] is False
