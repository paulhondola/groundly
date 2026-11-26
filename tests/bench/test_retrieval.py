import pytest

pytest.importorskip("duckdb")  # bench group only; CI installs `--extra dev`

from bench.retrieval.run import metrics, query_rows


def test_metrics_exact_and_within_one():
    labelled = [
        {"query": "q1", "file": "a.pdf", "page": 5},
        {"query": "q2", "file": "b.pdf", "page": 2},
    ]
    hits = [[("a.pdf", 6), ("a.pdf", 5)], [("c.pdf", 2)]]
    m = metrics(labelled, hits)
    assert m["hit_exact@8"] == 0.5 and m["hit_within1@8"] == 0.5 and m["mrr_within1@8"] == 0.5


def test_query_rows_mark_hits_and_latency():
    labelled = [
        {"query": "q1", "file": "a.pdf", "page": 5},
        {"query": "q2", "file": "b.pdf", "page": 2},
    ]
    hits = [[("a.pdf", 6, 0.9), ("x.pdf", 1, 0.1)], [("c.pdf", 2, 0.5)]]
    queries, ranked = query_rows(labelled, hits, [12.0, 30.0])
    assert [q["hit_within1_at8"] for q in queries] == [True, False]
    assert [q["latency_ms"] for q in queries] == [12.0, 30.0]
    assert len(ranked) == 3
    assert ranked[0] == {"query_id": "0", "rank": 1, "filename": "a.pdf", "page": 6, "score": 0.9}
