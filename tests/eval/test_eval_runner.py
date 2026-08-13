"""groundly/eval/runner.py: the eval loop and its results document."""

import json

import pytest
from llama_index.core.schema import NodeWithScore, TextNode

from groundly.eval.runner import run, write_results
from groundly.retrieval.graph import GraphNotBuiltError

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


def _stub_retrieve(monkeypatch, by_arm):
    def _retrieve(subject, query, arm, **kwargs):
        return _nodes(*by_arm[arm]), ["stub"]

    monkeypatch.setattr("groundly.eval.runner.retrieve_for_arm", _retrieve)
    # A test that fakes retrieval has no real subject on disk, so the graph preflight
    # would refuse every graph arm before scoring anything. Satisfying it here rather
    # than per test keeps the gate itself testable in exactly one place, below.
    monkeypatch.setattr("groundly.core.subject.Subject.graph_is_built", lambda self: True)


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


def test_run_measures_leakage_against_every_question_source(gold_file, monkeypatch):
    """Chunk 9 is Examen.md — a question source. Retrieving it is leakage, not a hit,
    and that holds for q2 too even though q2 declares no `source_file` of its own.
    Scoping leakage per row understated apd's real contamination by ~37%."""
    _stub_retrieve(monkeypatch, {"vector": [9]})
    results = run("TEST", gold_file, StubStore(), arms=["vector"])

    rows = {r["question_id"]: r for r in results["rows"]}
    assert rows["q1"]["leakage"] == 1.0
    assert rows["q1"]["hit"] is False
    assert rows["q2"]["leakage"] == 1.0  # hand-written, but still retrieving exam text


def test_eval_preflights_the_graph_requirement(gold_file, monkeypatch):
    """A missing graph is a configuration fact, not a per-question failure: it holds for
    every remaining question, so the run refuses *before* question 1 rather than filing
    48 identical errors. Retrieval is deliberately not stubbed — reaching it at all would
    mean the preflight ran too late."""

    def _unreachable(*a, **kw):
        raise AssertionError("the preflight must refuse before any retrieval")

    monkeypatch.setattr("groundly.eval.runner.retrieve_for_arm", _unreachable)
    with pytest.raises(GraphNotBuiltError, match="graph-global cannot be scored"):
        run("TEST", gold_file, StubStore(), arms=["vector", "graph-global"])


def test_eval_needs_no_graph_for_the_zero_key_arm(gold_file, monkeypatch):
    """The preflight keys off `needs_graph`, not off "any arm at all" — the vector arm
    stays runnable on a subject that was never graphed, which is the zero-key path."""
    _stub_retrieve(monkeypatch, {"vector": [1]})
    monkeypatch.setattr("groundly.core.subject.Subject.graph_is_built", lambda self: False)
    assert run("TEST", gold_file, StubStore(), arms=["vector"])["questions"] == 2


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
    # Two stale expected labels, plus the unresolvable question source.
    assert len(results["warnings"]) == 3
    assert any("expected lec.pdf p.7" in w for w in results["warnings"])
    assert any("source_file Examen.md" in w for w in results["warnings"])


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
        return _nodes(1), ["stub"]

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
        return _nodes(2), ["stub"]

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
        return _nodes(1), ["stub"]

    monkeypatch.setattr("groundly.eval.runner.retrieve_for_arm", _retrieve)
    results = run("TEST", gold_file, StubStore(), arms=["vector"])

    assert results["partial"] is True
    assert len(results["rows"]) == 1  # q1 kept, q2 never completed
    assert results["rows"][0]["question_id"] == "q1"


def test_a_complete_run_is_not_marked_partial(gold_file, monkeypatch):
    _stub_retrieve(monkeypatch, {"vector": [1]})
    assert run("TEST", gold_file, StubStore(), arms=["vector"])["partial"] is False


def test_unknown_arm_refuses_before_running_anything(gold_file, monkeypatch):
    """A typo'd arm must not be absorbed by per-question error tolerance. `retrieve_for_arm`
    raises ValueError for it, and `except Exception` used to file that as an outage — the
    run then wrote a results file that looked like a flaky provider instead of refusing."""
    called = []
    monkeypatch.setattr(
        "groundly.eval.runner.retrieve_for_arm",
        lambda *a, **k: called.append(1),
    )
    with pytest.raises(ValueError, match="unknown retrieval arm\\(s\\): vektor"):
        run("TEST", gold_file, StubStore(), arms=["vektor"])
    assert called == []  # refused before the first retrieval


def test_a_bug_in_an_arm_crashes_instead_of_scoring_as_a_provider_outage(gold_file, monkeypatch):
    """Error tolerance is for outages and context overflows. A KeyError in the node
    metadata contract means the code is broken; reporting it as an 'error' row would
    publish a table resting on a silently broken arm. TypeError is how the text-unit
    collision bug in retrieval/graph.py presented before it was fixed."""

    def _retrieve(*a, **k):
        raise KeyError("chunk_id")

    monkeypatch.setattr("groundly.eval.runner.retrieve_for_arm", _retrieve)
    with pytest.raises(KeyError):
        run("TEST", gold_file, StubStore(), arms=["vector"])


def test_an_unranked_arm_reports_no_mrr_rather_than_a_meaningless_one(gold_file, monkeypatch):
    """graph-global emits sorted(chunk_ids) — ascending rowid, no relevance order. An MRR
    over that measures how the corpus was indexed, and 0.02 was published as if it were
    evidence about ranking."""
    _stub_retrieve(monkeypatch, {"graph-global": [1, 2], "vector": [1, 2]})
    results = run("TEST", gold_file, StubStore(), arms=["graph-global", "vector"])

    per_arm = {a["slice"]["arm"]: a for a in results["by_arm"]}
    assert per_arm["graph-global"]["mrr"] is None
    assert per_arm["vector"]["mrr"] is not None  # ranked arms still report it
    # Order-insensitive metrics stay valid for the unranked arm.
    assert per_arm["graph-global"]["hit_rate"] == per_arm["vector"]["hit_rate"]
    assert per_arm["graph-global"]["recall"] == per_arm["vector"]["recall"]


def test_significance_is_computed_at_matched_cutoffs(tmp_path, monkeypatch):
    """Testing an arm that returns 42 chunks against one that returns 20 re-introduces the
    set-size confound the at_k table removes. Measured on apd, the unmatched comparison
    read as a tie (p = 1.000) while every matched cutoff favoured the baseline."""
    from groundly.eval.metrics import Scored, mcnemar

    def _row(qid, arm, retrieved, expected):
        return Scored.score(
            question=type("Q", (), {"id": qid, "klass": "factoid", "lang": "en"})(),
            arm=arm,
            retrieved=retrieved,
            expected=expected,
            source=set(),
        )

    # `wide` finds the labelled chunk only because it returns far more candidates;
    # `narrow` puts it at rank 1. At natural sizes they tie; at k=1 they do not.
    wide = [_row(f"q{i}", "wide", [9, 8, 7, 6, 1], {1}) for i in range(6)]
    narrow = [_row(f"q{i}", "narrow", [1], {1}) for i in range(6)]

    assert mcnemar(wide, narrow) == (0, 0, 1.0)  # unmatched: a tie
    matched = mcnemar([r.at(1, set()) for r in wide], [r.at(1, set()) for r in narrow])
    assert matched[0] == 0 and matched[1] == 6  # matched: narrow wins every question
