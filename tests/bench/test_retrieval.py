from bench.common import results
from bench.common.compare import table
from bench.retrieval.run import metrics, row


def test_metrics_exact_and_within_one():
    labelled = [
        {"query": "q1", "file": "a.pdf", "page": 5},
        {"query": "q2", "file": "b.pdf", "page": 2},
    ]
    hits = [[("a.pdf", 6), ("a.pdf", 5)], [("c.pdf", 2)]]
    m = metrics(labelled, hits)
    assert m["hit_exact@8"] == 0.5 and m["hit_within1@8"] == 0.5 and m["mrr_within1@8"] == 0.5


def test_compare_table_accepts_retrieval_rows(monkeypatch):
    # machine() reads macOS sysctl/sw_vers; CI runs on Linux.
    monkeypatch.setattr(results, "machine", lambda: "M")
    rows = [
        row("S", arm, "apd", 10, 0, {"hit_within1@8": v})
        for arm, v in (("docling_standard", 0.8), ("mineru", 0.9))
    ]
    out = table(rows, "docling_standard", "hit_within1@8", None)
    assert "docling_standard" in out and "mineru" in out
