import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bench.common import monitor
from bench.extract import run
from bench.extract.run import collect, model_disk_mb
from bench.extract.score import SCORER_VERSION
from tests.bench.test_score import _ref_manifest


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


def test_extract_main_writes_run_and_pages(tmp_path, monkeypatch):
    pytest.importorskip("duckdb")
    from bench.common import store

    m = {**_ref_manifest(tmp_path), "version": 2}
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(m))
    refs = tmp_path / "ref"
    refs.mkdir()
    for p in m["pages"]:
        (refs / f"{p['sha256']}-{p['page']}.txt").write_text("alpha beta", encoding="utf-8")

    def fake_run(cmd, **kw):
        rows = [
            {"sha256": p["sha256"], "page": p["page"], "text": "alpha beta", "ms": 5.0,
             "error": None, "capped": False}
            for p in m["pages"]
        ]  # fmt: skip
        lines = [json.dumps(r) for r in [*rows, {"meta": {"load_s": 1.5}}]]
        Path(cmd[cmd.index("--out") + 1]).write_text("\n".join(lines) + "\n")
        now = datetime.now(UTC)
        samples = {
            "sample_proc": [{"t_s": 0.0, "rss_mb": 10.0}],
            "sample_power": [],
            "sample_task": [],
        }
        return monitor.Measurement(1.0, 10.0, 0, now, now, samples, None)

    monkeypatch.setattr(run.monitor, "run", fake_run)
    monkeypatch.setattr(store, "STORE", tmp_path / "b.duckdb")
    argv = ["x", "--stack", "stub", "--manifest", str(manifest), "--reference-dir", str(refs)]
    monkeypatch.setattr(sys, "argv", [*argv, "--no-power"])
    run.main()
    con = store.connect(tmp_path / "b.duckdb")
    assert con.execute("select count(*), max(items), max(load_s), max(power_sampler) from run").fetchone() == (
        1, 2, 1.5, "none",
    )  # fmt: skip
    assert con.execute("select count(*), count(f1), max(scorer_version) from page").fetchone() == (
        2, 2, SCORER_VERSION,
    )  # fmt: skip
    assert con.execute("select count(*) from page_text").fetchone()[0] == 2
    assert con.execute("select count(*) from sample_proc").fetchone()[0] == 1
