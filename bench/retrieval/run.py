"""Retrieval bench: labelled queries through Groundly's `search` against an index a stack built.

A hit matches on equal filename and page (exact) or page within one (within1); the page
tolerance absorbs extractors that split or merge pages differently."""

import argparse
import json
import os
import sys
from pathlib import Path

from bench.common import results

ROOT = Path(__file__).parents[2]


def metrics(
    labelled: list[dict], hits: list[list[tuple[str, int | None]]], k: int = 8
) -> dict[str, float]:
    exact = within1 = mrr = 0.0
    for lab, got in zip(labelled, hits):
        got = got[:k]
        same_file = [(f == lab["file"], p) for f, p in got]
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


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m bench.retrieval.run")
    ap.add_argument("--home", type=Path, required=True)
    ap.add_argument("--subject", required=True)
    ap.add_argument("--queries", type=Path, required=True)
    ap.add_argument("--arm", required=True)
    args = ap.parse_args()

    os.environ["GROUNDLY_HOME"] = str(args.home.expanduser())
    from groundly.retrieval import vector

    labelled = json.loads(args.queries.read_text())
    hits = [
        [(h.filename, h.page) for h in vector.search(args.subject, lab["query"], k=8)]
        for lab in labelled
    ]
    m = metrics(labelled, hits)
    results.append(
        "retrieval",
        {
            "bench": "retrieval",
            "session": results.new_session(),
            "arm": args.arm,
            "sha": results._sh("git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"),
            "machine": results.machine(),
            "versions": results.versions(Path(sys.executable)),
            "subject": args.subject,
            "queries": len(labelled),
            "memray": False,
            "metrics": m,
        },
    )
    print(f"{args.arm}: {m}")


if __name__ == "__main__":
    main()
