"""groundly/eval/metrics.py: pure scoring functions over ranked id lists — no store,
no model, no key."""

import pytest
from groundly.eval.metrics import (
    Scored,
    aggregate,
    by_slice,
    hit,
    leakage,
    recall,
    reciprocal_rank,
)


class _Q:
    def __init__(self, qid="q1", klass="factoid", lang="en"):
        self.id, self.klass, self.lang = qid, klass, lang


def test_hit_is_true_on_any_overlap():
    assert hit([5, 1, 9], {1, 2}) is True
    assert hit([5, 9], {1, 2}) is False


def test_hit_is_false_when_nothing_was_labelled():
    """No labels means nothing to hit — never credit an arm for an unlabelled question."""
    assert hit([1, 2, 3], set()) is False


def test_recall_is_the_labelled_fraction_found():
    assert recall([1, 2, 7], {1, 2, 3, 4}) == 0.5
    assert recall([], {1}) == 0.0
    assert recall([1], set()) == 0.0


def test_reciprocal_rank_uses_the_first_labelled_position():
    assert reciprocal_rank([1, 2], {1}) == 1.0
    assert reciprocal_rank([9, 8, 1], {1}) == 1 / 3
    assert reciprocal_rank([9, 8], {1}) == 0.0


def test_reciprocal_rank_takes_the_earliest_of_several_labels():
    assert reciprocal_rank([9, 3, 1], {1, 3}) == 0.5


def test_leakage_is_the_share_retrieved_from_the_question_source():
    assert leakage([1, 2, 3, 4], {1, 2}) == 0.5
    assert leakage([1, 2], set()) == 0.0
    assert leakage([], {1}) == 0.0


def test_scored_captures_every_metric_for_one_question_arm():
    s = Scored.score(
        question=_Q(),
        arm="vector",
        retrieved=[9, 1, 3],
        expected={1, 2},
        source={3},
        latency_ms=42,
    )
    assert (s.question_id, s.arm, s.klass, s.lang) == ("q1", "vector", "factoid", "en")
    assert s.hit is True
    assert s.recall == 0.5
    assert s.reciprocal_rank == 0.5
    assert s.leakage == 1 / 3
    assert s.latency_ms == 42


def _scored(arm, klass, hit_ids, expected, latency=10):
    return Scored.score(
        question=_Q(f"{arm}-{klass}", klass),
        arm=arm,
        retrieved=hit_ids,
        expected=expected,
        source=set(),
        latency_ms=latency,
    )


def test_aggregate_averages_across_questions():
    rows = [_scored("vector", "factoid", [1], {1}), _scored("vector", "factoid", [9], {1})]
    agg = aggregate(rows, arm="vector")
    assert agg.n == 2
    assert agg.hit_rate == 0.5
    assert agg.mrr == 0.5
    assert agg.median_latency_ms == 10
    assert agg.slice == {"arm": "vector"}


def test_aggregate_of_nothing_is_zero_not_a_crash():
    agg = aggregate([])
    assert (agg.n, agg.hit_rate, agg.median_latency_ms) == (0, 0.0, None)
    assert agg.mrr is None  # no ranked row to average — not a real 0.0


def test_by_slice_groups_on_the_requested_keys():
    rows = [
        _scored("vector", "factoid", [1], {1}),
        _scored("vector", "global", [9], {1}),
        _scored("graph-global", "global", [1], {1}),
    ]
    per_arm = {a.slice["arm"]: a for a in by_slice(rows, "arm")}
    assert per_arm["vector"].n == 2 and per_arm["vector"].hit_rate == 0.5
    assert per_arm["graph-global"].n == 1 and per_arm["graph-global"].hit_rate == 1.0

    per_pair = by_slice(rows, "arm", "klass")
    assert len(per_pair) == 3
    assert all({"arm", "klass"} == set(a.slice) for a in per_pair)


def test_scored_records_the_retrieved_set_size():
    """Arms return incomparable set sizes (apd: vector 8, graph-global 1138), so the
    size travels with every score — hit rate and recall mean nothing without it."""
    s = Scored.score(
        question=_Q(), arm="graph-global", retrieved=[1, 2, 3], expected={1}, source=set()
    )
    assert s.retrieved_n == 3


def test_aggregate_reports_the_median_retrieved_set_size():
    rows = [
        _scored("vector", "factoid", [1] * 8, {1}),
        _scored("vector", "factoid", [1] * 8, {1}),
        _scored("vector", "factoid", [1] * 400, {1}),
    ]
    assert aggregate(rows, arm="vector").median_retrieved_n == 8


def _row(qid, arm, retrieved, expected, *, hit_=True, err=None):
    from groundly.eval.metrics import Scored

    return Scored(
        question_id=qid,
        arm=arm,
        klass="factoid",
        lang="en",
        retrieved=retrieved,
        retrieved_n=len(retrieved),
        hit=hit_,
        recall=0.0,
        reciprocal_rank=0.0,
        leakage=0.0,
        expected=expected,
        error=err,
    )


def test_at_rescores_against_only_the_top_k():
    """The whole point of storing the full candidate list plus its labels: a finished
    results file can be re-cut at any k without the store that produced it."""
    from groundly.eval.metrics import Scored

    row = Scored.score(
        question=_Q(),
        arm="hybrid-local",
        retrieved=[9, 8, 7, 3],
        expected={3},
        source=set(),
    )
    assert row.hit is True and row.reciprocal_rank == pytest.approx(0.25)

    cut = row.at(2, set())
    assert cut.retrieved == [9, 8]
    assert cut.hit is False
    assert cut.recall == 0.0
    assert cut.reciprocal_rank == 0.0
    # the un-cut row is untouched — `at` returns a new row, it does not mutate
    assert row.hit is True


def test_at_keeps_mrr_none_for_unranked_arms():
    """An arm with no relevance order still has none after truncation."""
    from groundly.eval.metrics import Scored

    row = Scored.score(
        question=_Q(),
        arm="graph-global",
        retrieved=[1, 2, 3],
        expected={3},
        source=set(),
        ranked=False,
    )
    assert row.at(2, set()).reciprocal_rank is None


def test_sweep_labels_every_slice_with_its_k():
    from groundly.eval.metrics import sweep

    rows = [_row("q1", "vector", [5, 1], [1]), _row("q1", "graph", [1, 5], [1])]
    aggs = sweep(rows, [1, 2], set(), "arm")
    by = {(a.slice["arm"], a.slice["k"]): a for a in aggs}
    assert set(by) == {("vector", "1"), ("vector", "2"), ("graph", "1"), ("graph", "2")}
    # at k=1 only `graph` has the labelled chunk; at k=2 both do
    assert by[("graph", "1")].hit_rate == 1.0
    assert by[("vector", "1")].hit_rate == 0.0
    assert by[("vector", "2")].hit_rate == 1.0


def test_sweep_passes_errored_rows_through_untouched():
    """An errored row has no candidate list to truncate and must stay excluded, not
    become a miss at every k."""
    from groundly.eval.metrics import sweep

    rows = [_row("q1", "vector", [], [1], err="provider down")]
    agg = sweep(rows, [5], set(), "arm")[0]
    assert agg.n == 0 and agg.errors == 1


def test_mcnemar_reproduces_the_published_split_as_a_tie():
    """1 win / 4 losses over 48 questions — the exact split the GraphRAG review reported
    as 'net -3'. Five discordant pairs cannot clear p < 0.05 at any imbalance."""
    from groundly.eval.metrics import mcnemar

    a = [_row(f"q{i}", "hybrid", [], [], hit_=i < 1) for i in range(48)]
    b = [_row(f"q{i}", "vector", [], [], hit_=1 <= i < 5) for i in range(48)]
    arm_only, baseline_only, p = mcnemar(a, b)
    assert (arm_only, baseline_only) == (1, 4)
    assert p == pytest.approx(0.375)


def test_mcnemar_detects_a_real_effect_and_handles_no_disagreement():
    from groundly.eval.metrics import mcnemar

    a = [_row(f"q{i}", "a", [], [], hit_=True) for i in range(10)]
    b = [_row(f"q{i}", "b", [], [], hit_=False) for i in range(10)]
    assert mcnemar(a, b)[2] < 0.05
    assert mcnemar(a, a) == (0, 0, 1.0)


def test_mcnemar_drops_errored_rows_from_both_sides():
    """An outage on one arm is not a win for the other."""
    from groundly.eval.metrics import mcnemar

    a = [_row("q1", "a", [], [], hit_=True), _row("q2", "a", [], [], err="down")]
    b = [_row("q1", "b", [], [], hit_=True), _row("q2", "b", [], [], hit_=True)]
    assert mcnemar(a, b) == (0, 0, 1.0)


def test_sweep_omits_unranked_arms_entirely():
    """`graph-global` emits sorted(chunk_ids) — ascending rowid. Its 'top 8' is the eight
    lowest-numbered chunks in the corpus, a number that looks like precision@8 and
    measures ingestion order. It is withheld for the same reason MRR is."""
    from groundly.eval.metrics import sweep, unranked_arms

    ranked = Scored.score(
        question=_Q("q1"), arm="vector", retrieved=[1, 2], expected={2}, source=set()
    )
    unranked = Scored.score(
        question=_Q("q1"),
        arm="graph-global",
        retrieved=[1, 2],
        expected={2},
        source=set(),
        ranked=False,
    )

    aggs = sweep([ranked, unranked], [1, 2], set(), "arm")
    assert {a.slice["arm"] for a in aggs} == {"vector"}
    assert unranked_arms([ranked, unranked]) == ["graph-global"]


def test_sweep_keeps_errored_rows_of_ranked_arms():
    """An error is not the same as having no rank — the row must still be counted as an
    error rather than dropped from the table along with the unranked arms."""
    from groundly.eval.metrics import sweep

    failed = Scored.failed(question=_Q("q1"), arm="vector", error="provider down")
    aggs = sweep([failed], [5], set(), "arm")
    assert len(aggs) == 1 and aggs[0].errors == 1 and aggs[0].n == 0
