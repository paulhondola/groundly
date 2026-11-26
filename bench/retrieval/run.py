"""Retrieval bench: labelled queries through Groundly's `search` against an index a stack built.

A hit matches on equal filename and page (exact) or page within one (within1); the page
tolerance absorbs extractors that split or merge pages differently.

The searches run in a measured child (`--one`), so the parent loads no model; one warm-up
search, timed as `load_s`, loads the models before the timed queries."""

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from bench.common import monitor, results
from bench.common.sampler import power_session

ROOT = Path(__file__).parents[2]


def metrics(labelled: list[dict], hits: list[list[tuple]], k: int = 8) -> dict[str, float]:
    """Hits are (filename, page) or (filename, page, score)."""
    exact = within1 = mrr = 0.0
    for lab, got in zip(labelled, hits):
        got = got[:k]
        same_file = [(h[0] == lab["file"], h[1]) for h in got]
        exact += any(ok and p == lab["page"] for ok, p in same_file)
        rank = next(
            (
                i
                for i, (ok, p) in enumerate(same_file, 1)
                if ok and p is not None and abs(p - lab["page"]) <= 1
            ),
            None,
        )
        within1 += rank is not None
        mrr += 1 / rank if rank else 0.0
    n = len(labelled)
    return {
        f"hit_exact@{k}": exact / n,
        f"hit_within1@{k}": within1 / n,
        f"mrr_within1@{k}": mrr / n,
    }


def query_rows(
    labelled: list[dict], hits: list[list[tuple]], latencies_ms: list[float], k: int = 8
) -> tuple[list[dict], list[dict]]:
    """`query` rows (latency, hit within one page in the top k) and `query_hit` rows."""
    queries, ranked = [], []
    for i, (lab, got, ms) in enumerate(zip(labelled, hits, latencies_ms, strict=True)):
        qid = str(lab.get("id", i))
        hit = metrics([lab], [got], k)[f"hit_within1@{k}"] == 1.0
        queries.append({"query_id": qid, "latency_ms": ms, "hit_within1_at8": hit})
        ranked += [
            {"query_id": qid, "rank": r, "filename": h[0], "page": h[1],
             "score": h[2] if len(h) > 2 else None}
            for r, h in enumerate(got, 1)
        ]  # fmt: skip
    return queries, ranked


def _one(subject: str, labelled: list[dict], out: Path) -> None:
    from groundly.retrieval import vector

    t0 = time.perf_counter()
    vector.search(subject, labelled[0]["query"], k=8)  # loads the embedder and reranker
    load_s = time.perf_counter() - t0
    hits, latencies = [], []
    for lab in labelled:
        t = time.perf_counter()
        got = vector.search(subject, lab["query"], k=8)
        latencies.append((time.perf_counter() - t) * 1000)
        hits.append([(h.filename, h.page, h.score) for h in got])
    out.write_text(json.dumps({"load_s": load_s, "hits": hits, "latencies_ms": latencies}))


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m bench.retrieval.run")
    ap.add_argument("--home", type=Path, required=True)
    ap.add_argument("--subject", required=True)
    ap.add_argument("--queries", type=Path, required=True)
    ap.add_argument("--arm", required=True)
    ap.add_argument("--session")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--no-power", action="store_true")
    ap.add_argument("--one", type=Path, help=argparse.SUPPRESS)  # child: write results here
    args = ap.parse_args()

    labelled = json.loads(args.queries.read_text())
    if not labelled:
        sys.exit(f"no queries in {args.queries}")
    if args.one:
        return _one(args.subject, labelled, args.one)

    from bench.common import store

    session = args.session or results.new_session()
    info = results.session_info(session, Path(sys.executable))
    env = {**os.environ, "GROUNDLY_HOME": str(args.home.expanduser()), "PYTHONPATH": str(ROOT)}
    with power_session(args.no_power) as power:
        idle = power.idle_mw() if power else None
        for run_no in range(1, args.runs + 1):
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "hits.json"
                cmd = [sys.executable, "-m", "bench.retrieval.run", "--home", str(args.home)]
                cmd += ["--subject", args.subject, "--queries", str(args.queries)]
                cmd += ["--arm", args.arm, "--one", str(out)]
                m = monitor.run(cmd, env=env, cwd=ROOT, power=power)
                got = json.loads(out.read_text()) if out.is_file() else None
            children: dict[str, list[dict]] = dict(m.samples)
            if got:  # a failed search leaves only the run row, so compare reports it dropped
                children["query"], children["query_hit"] = query_rows(
                    labelled, got["hits"], got["latencies_ms"]
                )
            run = {
                "session": session, "bench": "retrieval", "arm": args.arm, "subject": args.subject,
                "run_no": run_no, "load_s": got and got["load_s"], "items": len(labelled),
                **monitor.run_columns(m, idle, power),
            }  # fmt: skip
            store.save(info, run, children)
            hits = got and metrics(labelled, got["hits"])
            print(f"{args.arm} run {run_no}: rc={m.returncode} {hits or 'search failed'}")
    print(f"session: {session}")


if __name__ == "__main__":
    main()
