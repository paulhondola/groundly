"""Same-session comparison of arms against a baseline arm.

Only rows from one session on one machine and manifest are comparable, so the
table refuses anything else rather than averaging across them.
"""

import argparse
import json
import statistics
import sys

from bench.common.results import RESULTS_DIR


def table(rows: list[dict], baseline: str, metric: str, gate: float | None) -> str:
    sessions = {r["session"] for r in rows if r["arm"] == baseline}
    if not sessions:
        raise ValueError(f"no rows for baseline arm {baseline!r}")
    rows = [r for r in rows if r["session"] == max(sessions)]
    for field in ("machine", "manifest_version"):
        if len({r[field] for r in rows}) > 1:
            raise ValueError(f"mixed {field} within session {max(sessions)}")

    failed = sum(r["returncode"] != 0 for r in rows)
    values: dict[str, list[float]] = {}
    for r in rows:
        if r["returncode"] == 0:
            values.setdefault(r["arm"], []).append(r["metrics"][metric])
    if baseline not in values:
        raise ValueError(f"every run of baseline arm {baseline!r} failed")

    base = statistics.median(values[baseline])
    lines = [f"session {max(sessions)}  metric {metric}  baseline {baseline}"]
    for arm, v in values.items():
        med = statistics.median(v)
        ratio = med / base
        verdict = ""
        if arm != baseline and gate is not None:
            verdict = "  PASS" if ratio <= gate else "  FAIL"
        lines.append(
            f"{arm:<20} n={len(v)}  median {med:.1f}  "
            f"range {min(v):.1f}-{max(v):.1f}  ratio {ratio:.2f}{verdict}"
        )
    if failed:
        lines.append(f"{failed} failed run{'s' if failed > 1 else ''} dropped")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m bench.common.compare")
    ap.add_argument("bench")
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--metric", default="wall_s")
    ap.add_argument("--session")
    ap.add_argument("--gate", type=float)
    a = ap.parse_args()
    path = RESULTS_DIR / f"{a.bench}.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    if a.session:
        rows = [r for r in rows if r["session"] == a.session]
    try:
        print(table(rows, a.baseline, a.metric, a.gate))
    except ValueError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
