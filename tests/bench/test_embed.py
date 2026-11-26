import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("duckdb")  # bench group only; CI installs `--extra dev`

from bench.embed.run import agreement_rows

ROOT = Path(__file__).parents[2]


def npz(path: Path, scores: list[float]) -> Path:
    rng = np.random.default_rng(0)
    np.savez(
        path,
        chunk_ids=np.array([1, 2]),
        dense=rng.standard_normal((2, 8)).astype(np.float32),
        sparse_ids=np.array([[5, 6, 7, -1], [8, 9, -1, -1]]),
        rerank_qids=np.array(["q"] * 6),
        rerank_cids=np.arange(10, 16),
        rerank_scores=np.array(scores),
    )
    return path


def test_agreement_rows_on_identical_outputs_are_perfect(tmp_path):
    scores = [0.9, 0.8, 0.7, 0.6, 0.5, 0.1]
    rows = agreement_rows(npz(tmp_path / "a.npz", scores), npz(tmp_path / "b.npz", scores))
    chunks = [r for r in rows if r["item_kind"] == "chunk"]
    (query,) = [r for r in rows if r["item_kind"] == "query"]
    assert len(chunks) == 2
    assert all(
        r["dense_cos"] == pytest.approx(1) and r["sparse_top10_overlap"] == 1 for r in chunks
    )
    assert query["rerank_top5_overlap"] == 1 and query["max_score_diff"] == 0


def test_agreement_rows_see_a_swap(tmp_path):
    a = npz(tmp_path / "a.npz", [0.9, 0.8, 0.7, 0.6, 0.5, 0.1])
    b = npz(tmp_path / "b.npz", [0.9, 0.8, 0.7, 0.6, 0.05, 0.1])  # 5th and 6th trade places
    (query,) = [r for r in agreement_rows(a, b) if r["item_kind"] == "query"]
    assert query["rerank_top5_overlap"] == pytest.approx(0.8)
    assert query["max_score_diff"] == pytest.approx(0.45)


@pytest.mark.slow
def test_torch_arm_really_runs_torch(tmp_path):
    from bench.index.run import scratch_home

    home = scratch_home()
    try:
        _index_and_embed_with_torch(home, tmp_path)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def _index_and_embed_with_torch(home: Path, tmp_path: Path) -> None:
    doc = tmp_path / "notes.md"
    doc.write_text("# Caches\n\nA cache keeps recent data close to the processor.\n\n" * 3)
    env = {**os.environ, "GROUNDLY_HOME": str(home), "PYTHONPATH": str(ROOT)}
    index = [sys.executable, "-m", "bench.index.run", "--one", "--stack", "product"]
    subprocess.run([*index, "--subject", "t", "--input", str(doc)], env=env, cwd=ROOT, check=True)
    queries = tmp_path / "q.json"
    queries.write_text(
        json.dumps([{"query": "what does a cache keep", "file": "notes.md", "page": 1}])
    )
    facts, out = tmp_path / "facts.json", tmp_path / "out.npz"
    child = [sys.executable, "-m", "bench.embed.child", "--engine", "torch", "--home", str(home)]
    child += ["--subject", "t", "--queries", str(queries), "--out", str(out), "--facts", str(facts)]
    subprocess.run(child, env=env, cwd=ROOT, check=True)
    got = json.loads(facts.read_text())
    assert got["device"] != "mlx" and got["chunks"] >= 1 and len(got["latencies_ms"]) == 1
    assert np.load(out)["dense"].shape[1] == 1024
