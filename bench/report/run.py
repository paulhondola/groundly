"""Report: R1–R15 from the store, each as Parquet and CSV under bench/results/export/, then
one PNG figure per result drawn from the Parquet files.

The queries live in `queries.sql`; R1's bootstrap CIs are computed here over the page rows.
Energy is the trapezoid integral of combined power over a run's samples minus its idle
baseline × wall time; CPU % is Δcpu_s / Δt_s. memray runs feed only R15.
"""

import argparse
import random
import re
from pathlib import Path

from bench.common import store
from bench.common.results import RESULTS_DIR

EXPORT_DIR = RESULTS_DIR / "export"
_BLOCKS = re.split(
    r"^-- name: (\S+)\n", (Path(__file__).parent / "queries.sql").read_text(), flags=re.MULTILINE
)
SQL = dict(zip(_BLOCKS[1::2], (b.strip() for b in _BLOCKS[2::2]), strict=True))
RESULTS: dict[str, str] = {k: v.rstrip(";") for k, v in SQL.items() if k.startswith("R")}


def bootstrap_ci(values: list[float], n: int = 2000, seed: int = 0) -> tuple[float, float]:
    """95% percentile bootstrap interval of the mean."""
    if len(values) < 2:
        return (values[0], values[0]) if values else (float("nan"), float("nan"))
    rng = random.Random(seed)
    means = sorted(sum(rng.choices(values, k=len(values))) / len(values) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n) - 1]


def _select(con, session: str | None) -> None:
    con.execute(
        "create or replace temp table sel_ids as select id from run r "
        "where session = coalesce(cast(? as varchar), "
        "(select max(session) from run r2 where r2.bench = r.bench))",
        [session],
    )
    con.execute(SQL["setup"])
    con.execute("create or replace temp table f1_ci (arm varchar, kind varchar, lang varchar, "
                "f1_lo double, f1_hi double)")  # fmt: skip
    rows = [(a, k, la, *bootstrap_ci(v)) for a, k, la, v in con.execute(SQL["f1_lists"]).fetchall()]
    if rows:
        con.executemany("insert into f1_ci values (?, ?, ?, ?, ?)", rows)


def export(con, out_dir: Path, session: str | None = None) -> list[Path]:
    """Write every result as `<R>.parquet` and `<R>.csv`; the latest session per bench by default."""
    out_dir.mkdir(parents=True, exist_ok=True)
    _select(con, session)
    written = []
    for name, sql in RESULTS.items():
        for ext, opts in (("parquet", "format parquet"), ("csv", "format csv, header")):
            path = out_dir / f"{name}.{ext}"
            con.execute(f"copy ({sql}) to '{str(path).replace(chr(39), chr(39) * 2)}' ({opts})")
            written.append(path)
    return written


def figures(out_dir: Path) -> list[Path]:
    from bench.report.figures import draw

    return [draw(name, out_dir / f"{name}.parquet", out_dir / f"{name}.png") for name in RESULTS]


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m bench.report.run")
    ap.add_argument("--session", help="one session for every bench (default: each bench's latest)")
    ap.add_argument("--out", type=Path, default=EXPORT_DIR)
    args = ap.parse_args()
    con = store.connect()
    try:
        files = export(con, args.out, args.session)
    finally:
        con.close()
    pngs = figures(args.out)
    print(f"{len(files)} exports and {len(pngs)} figures in {args.out}")


if __name__ == "__main__":
    main()
