"""Same-session comparison of arms against a baseline arm, read from the store.

Only rows from one session on one machine and manifest are comparable, so the
table refuses anything else rather than averaging across them. memray runs are
never compared: tracing distorts time.
"""

import argparse
import statistics
import sys

from bench.common import store


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


_RUN_METRICS = ("wall_s", "load_s", "items")
_PER_RUN = {
    "peak_rss_mb": "select run, max(rss_mb) from sample_proc group by run",
    "hit_within1@8": "select run, avg(hit_within1_at8::int) from query group by run",
    "ms_p50": "select run, median(ms) from page where error is null group by run",
}


def rows_from_store(con, bench: str) -> list[dict]:
    """The bench's non-memray runs as `table` rows; per-run metrics are aggregated here."""
    runs = con.execute(
        f"select r.id, r.session, r.arm, s.machine, r.manifest_version, r.returncode, "
        f"{', '.join('r.' + c for c in _RUN_METRICS)} from run r "
        "left join session s on s.id = r.session where r.bench = ? and not r.memray order by r.id",
        [bench],
    ).fetchall()
    extra: dict[int, dict] = {}
    for name, sql in _PER_RUN.items():
        for rid, v in con.execute(sql).fetchall():
            extra.setdefault(rid, {})[name] = v
    for rid, kind, lang, f1 in con.execute(
        "select run, kind, lang, avg(f1) from page where f1 is not null group by all"
    ).fetchall():
        extra.setdefault(rid, {})[f"word_f1/{kind}/{lang}"] = f1
    rows = []
    for rid, session, arm, machine, manifest_version, rc, *vals in runs:
        m = {k: v for k, v in zip(_RUN_METRICS, vals, strict=True) if v is not None}
        rows.append(
            {"session": session, "arm": arm, "machine": machine, "returncode": rc,
             "manifest_version": manifest_version, "metrics": m | extra.get(rid, {})}
        )  # fmt: skip
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m bench.common.compare")
    ap.add_argument("bench")
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--metric", default="wall_s")
    ap.add_argument("--session")
    ap.add_argument("--gate", type=float)
    a = ap.parse_args()
    con = store.connect(read_only=True)
    try:
        rows = rows_from_store(con, a.bench)
    finally:
        con.close()
    if a.session:
        rows = [r for r in rows if r["session"] == a.session]
    try:
        print(table(rows, a.baseline, a.metric, a.gate))
    except ValueError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
