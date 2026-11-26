"""Embed bench: bge-m3 and the reranker on MLX vs torch, same Mac, same chunks and queries.

Arms run interleaved, each run a fresh measured child (`bench.embed.child`). After every
round the two arms' outputs are compared item by item into `agreement` rows: per chunk the
dense cosine and sparse top-10 overlap, per query the rerank top-5 overlap and the largest
score difference (decision 48's tolerance measures). Each run's npz is kept as an artifact.
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

from bench.common import monitor, results, store
from bench.common.sampler import power_session
from bench.retrieval.run import query_rows

ROOT = Path(__file__).parents[2]
EMBED_DIR = results.RESULTS_DIR / "embed"
ARMS = ("mlx", "torch")


def _scores(x, qid: str) -> dict[int, float]:
    pick = x["rerank_qids"] == qid
    return dict(
        zip(x["rerank_cids"][pick].tolist(), x["rerank_scores"][pick].tolist(), strict=True)
    )


def _top(scores: dict[int, float], n: int = 5) -> set[int]:
    return set(sorted(scores, key=scores.__getitem__, reverse=True)[:n])


def agreement_rows(a: Path, b: Path) -> list[dict]:
    """Item-by-item agreement of two runs' outputs (no run ids; the caller adds them)."""
    x, y = np.load(a), np.load(b)
    where = {int(c): j for j, c in enumerate(y["chunk_ids"])}
    rows: list[dict] = []
    for i, cid in enumerate(x["chunk_ids"].tolist()):
        j = where.get(cid)
        if j is None:
            continue
        da, db = x["dense"][i], y["dense"][j]
        sa = {int(t) for t in x["sparse_ids"][i] if t >= 0}
        sb = {int(t) for t in y["sparse_ids"][j] if t >= 0}
        rows.append(
            {"item_kind": "chunk", "item_id": str(cid),
             "dense_cos": float(da @ db / (np.linalg.norm(da) * np.linalg.norm(db))),
             "sparse_top10_overlap": len(sa & sb) / max(len(sa), len(sb)) if sa or sb else 1.0}
        )  # fmt: skip
    for qid in dict.fromkeys(x["rerank_qids"].tolist()):
        sa, sb = _scores(x, qid), _scores(y, qid)
        common = sa.keys() & sb.keys()
        rows.append(
            {"item_kind": "query", "item_id": qid,
             "rerank_top5_overlap": len(_top(sa) & _top(sb)) / max(1, min(5, len(sa))),
             "max_score_diff": max((abs(sa[c] - sb[c]) for c in common), default=None)}
        )  # fmt: skip
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m bench.embed.run")
    ap.add_argument("--subject", required=True)
    ap.add_argument("--home", type=Path, default=Path.home() / ".groundly")
    ap.add_argument("--queries", type=Path, required=True)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--no-power", action="store_true")
    args = ap.parse_args()

    labelled = json.loads(args.queries.read_text())
    session = results.new_session()
    info = results.session_info(session, Path(sys.executable))
    EMBED_DIR.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GROUNDLY_HOME": str(args.home.expanduser()), "PYTHONPATH": str(ROOT)}
    with power_session(args.no_power) as power:
        idle = power.idle_mw() if power else None
        for run_no in range(1, args.runs + 1):
            done: dict[str, tuple[int, Path]] = {}
            for arm in ARMS:
                out = EMBED_DIR / f"{session}-{arm}-{run_no}.npz"
                with tempfile.TemporaryDirectory() as tmp:
                    facts_path = Path(tmp) / "facts.json"
                    cmd = [sys.executable, "-m", "bench.embed.child", "--engine", arm]
                    cmd += ["--home", str(args.home), "--subject", args.subject]
                    cmd += ["--queries", str(args.queries), "--out", str(out)]
                    cmd += ["--facts", str(facts_path)]
                    m = monitor.run(cmd, env=env, cwd=ROOT, power=power)
                    facts = json.loads(facts_path.read_text()) if facts_path.is_file() else {}
                children: dict[str, list[dict]] = dict(m.samples)
                if facts:
                    children["query"], children["query_hit"] = query_rows(
                        labelled, facts["hits"], facts["latencies_ms"]
                    )
                    children["artifact"] = [{"kind": "npz", "path": str(out), "process": "child"}]
                run = {
                    "session": session, "bench": "embed", "arm": arm, "subject": args.subject,
                    "run_no": run_no, "device": facts.get("device"), "load_s": facts.get("load_s"),
                    "items": facts.get("chunks"), "embed_s": facts.get("embed_s"),
                    "rerank_s": facts.get("rerank_s"), "pairs": facts.get("pairs"),
                    **monitor.run_columns(m, idle, power),
                }  # fmt: skip
                rid = store.save(info, run, children)
                if facts:
                    done[arm] = (rid, out)
                print(f"{arm} run {run_no}: rc={m.returncode} {facts.get('device')} "
                      f"embed {facts.get('embed_s')} rerank {facts.get('rerank_s')}")  # fmt: skip
            if len(done) == len(ARMS):
                (ra, pa), (rb, pb) = done["mlx"], done["torch"]
                rows = [{**r, "run_a": ra, "run_b": rb} for r in agreement_rows(pa, pb)]
                con = store.connect()
                try:
                    store.insert(con, "agreement", rows)
                finally:
                    con.close()
    print(f"session: {session}")


if __name__ == "__main__":
    main()
