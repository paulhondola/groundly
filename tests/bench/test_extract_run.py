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
    assert [g["error"] for g in collect(manifest, out)[0]] == [None, "missing from stack output"]


def test_collect_splits_the_meta_row(tmp_path):
    manifest = {"version": 2, "pages": [page("aa", 1)]}
    r = {"sha256": "aa", "page": 1, "text": "x", "ms": 1.0, "error": None, "capped": False}
    out = tmp_path / "o.jsonl"
    out.write_text(json.dumps(r) + "\n" + json.dumps({"meta": {"load_s": 1.5}}) + "\n")
    assert collect(manifest, out) == ([r], {"load_s": 1.5})
