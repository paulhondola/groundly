import json

from bench.extract.run import collect


def page(sha, n):
    return {"sha256": sha, "page": n, "path_hint": "x", "ref": False}


def test_collect_marks_pages_the_stack_skipped(tmp_path):
    manifest = {"version": 1, "pages": [page("aa", 1), page("aa", 2)]}
    out = tmp_path / "o.jsonl"
    out.write_text(
        json.dumps({"sha256": "aa", "page": 1, "text": "x", "ms": 1.0, "error": None}) + "\n"
    )
    assert [g["error"] for g in collect(manifest, out)] == [None, "missing from stack output"]
