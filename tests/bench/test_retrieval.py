from bench.retrieval.run import metrics


def test_metrics_exact_and_within_one():
    labelled = [
        {"query": "q1", "file": "a.pdf", "page": 5},
        {"query": "q2", "file": "b.pdf", "page": 2},
    ]
    hits = [[("a.pdf", 6), ("a.pdf", 5)], [("c.pdf", 2)]]
    m = metrics(labelled, hits)
    assert m["hit_exact@8"] == 0.5 and m["hit_within1@8"] == 0.5 and m["mrr_within1@8"] == 0.5
