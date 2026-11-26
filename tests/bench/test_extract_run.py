import json

from bench.extract import run
from bench.extract.run import collect, model_disk_mb, pages_path


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


def test_model_disk_mb_sums_the_arm_repos(tmp_path, monkeypatch):
    for repo, mb in (("models--a--x", 1), ("models--b--y", 2)):
        snap = tmp_path / repo / "snapshots" / "rev"
        snap.mkdir(parents=True)
        (snap / "w.bin").write_bytes(b"0" * (mb << 20))
    monkeypatch.setattr(run, "MODEL_REPOS", {"arm": ("a/x", "b/y"), "gone": ("c/z",)})
    assert model_disk_mb("arm", cache=tmp_path) == 3.0
    assert model_disk_mb("gone", cache=tmp_path) is None
    assert model_disk_mb("unknown", cache=tmp_path) is None


def test_page_rows_are_kept_per_session_and_arm(tmp_path):
    assert pages_path("S1", "granite", tmp_path) == tmp_path / "pages" / "S1-granite.jsonl"
