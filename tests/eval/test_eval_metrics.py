"""groundly/eval/metrics.py: pure scoring functions over ranked id lists — no store,
no model, no key."""

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
    assert (agg.n, agg.hit_rate, agg.mrr, agg.median_latency_ms) == (0, 0.0, 0.0, None)


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
