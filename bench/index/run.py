"""Interleaved index runs: each run indexes a subject end to end through Groundly's real
IngestionPipeline in a scratch GROUNDLY_HOME, with a stack module as the extraction worker.

The parent stays light (no Groundly models, no torch/mlx); every Groundly import lives in
`_one`, a child whose process tree the monitor measures with its worker. Each run is written
to the store with its per-file rows; `--memray` traces both processes (the `_one` parent and
the extraction worker) and records each capture's peak heap and flamegraph."""

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from bench.common import monitor, results, store
from bench.common.sampler import power_session

if TYPE_CHECKING:
    from groundly.core.config import IngestionSettings

ROOT = Path(__file__).parents[2]
MODELS = Path.home() / ".groundly/models"


def schedule(stacks: list[str], runs: int) -> list[tuple[str, int]]:
    """Alternate stacks run by run so drift (thermals, cache) hits every stack alike."""
    return [(s, r) for r in range(runs) for s in stacks]


def scratch_home(models: Path = MODELS) -> Path:
    """A fresh Groundly home holding only a link to the already-downloaded models."""
    home = Path(tempfile.mkdtemp(prefix="groundly-bench-"))
    (home / "models").symlink_to(models)
    return home


def worker_argv(stack: str, memray_out: Path | None) -> list[str]:
    """The worker for an arm: `product` is the shipped extraction worker itself."""
    module = "groundly.ingestion.extract_worker" if stack == "product" else f"bench.stacks.{stack}"
    serve = ["-m", module, "--serve"]
    if memray_out is None:
        return [sys.executable, *serve]
    return [sys.executable, "-m", "memray", "run", "-q", "-o", str(memray_out), *serve]


def store_stats(home: Path, subject: str) -> dict:
    con = sqlite3.connect(home / subject / "store.db")
    try:
        failed = con.execute(
            "SELECT filename, error FROM materials WHERE status = 'extraction_failed' ORDER BY id"
        ).fetchall()
        return {
            "chunks": con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
            "materials": con.execute("SELECT COUNT(*) FROM materials").fetchone()[0],
            "failed": [{"filename": f, "error": e} for f, e in failed],
        }
    finally:
        con.close()


def timed_out(failed: list[dict]) -> int:
    return sum("timed out" in (f["error"] or "") for f in failed)


def file_seconds(marks: dict) -> list[dict]:
    """Each extracted file's extract and embed time from its stage timestamps ("extracting",
    "embedding", "end"). A file that fails extraction never reaches "embedding"; its extract
    time runs to its terminal event, so a timeout still counts. Unextracted files are left out."""
    out = []
    for path, m in marks.items():
        if "extracting" not in m:
            continue
        extract_s = m.get("embedding", m.get("end", m["extracting"])) - m["extracting"]
        embed_s = m["end"] - m["embedding"] if "embedding" in m and "end" in m else 0.0
        out.append({"file": Path(path).name, "extract_s": extract_s, "embed_s": embed_s})
    return out


def stage_seconds(marks: dict) -> tuple[float, float]:
    """Total extract and embed time over all files (see file_seconds)."""
    per = file_seconds(marks)
    return sum(f["extract_s"] for f in per), sum(f["embed_s"] for f in per)


def memray_artifacts(captures: dict[str, Path]) -> list[dict]:
    """`artifact` rows per existing capture: its peak heap, and a flamegraph HTML beside it."""
    rows = []
    for process, capture in captures.items():
        if not capture.is_file():
            continue
        stats, html = capture.with_suffix(".json"), capture.with_suffix(".html")
        memray = [sys.executable, "-m", "memray"]
        subprocess.run([*memray, "stats", "--json", "-f", "-o", str(stats), str(capture)],
                       check=True, capture_output=True)  # fmt: skip
        subprocess.run([*memray, "flamegraph", "-f", "-o", str(html), str(capture)],
                       check=True, capture_output=True)  # fmt: skip
        peak = json.loads(stats.read_text())["metadata"]["peak_memory"] / (1 << 20)
        rows += [
            {"kind": "memray", "path": str(capture), "process": process, "peak_heap_mb": peak},
            {"kind": "memray_html", "path": str(html), "process": process},
        ]
    return rows


def ingestion_settings(timeout: float | None) -> "IngestionSettings":
    """This machine's ingestion settings, the timeout overridden when given."""
    from groundly.core.config import load_settings

    settings = load_settings().ingestion
    return settings if timeout is None else settings.model_copy(update={"timeout_seconds": timeout})


def _one(
    stack: str,
    subject: str,
    input_path: Path,
    memray_out: Path | None,
    timing_out: Path | None,
    timeout: float | None = None,
) -> None:
    from groundly.core.subject import Subject
    from groundly.ingestion.extract import SubprocessExtractor
    from groundly.ingestion.pipeline import IngestionPipeline

    subj = Subject(subject)
    if not subj.exists():
        subj.initialize()
    marks: dict[Path, dict[str, float]] = {}

    def on_event(path: Path, stage: str) -> None:
        marks.setdefault(path, {})[stage if stage in ("extracting", "embedding") else "end"] = (
            time.perf_counter()
        )

    t0 = time.perf_counter()
    settings = ingestion_settings(timeout)
    with SubprocessExtractor(settings, argv=worker_argv(stack, memray_out)) as ex:
        IngestionPipeline(subj, extractor=ex, on_event=on_event).run([input_path])
    pipeline_s = time.perf_counter() - t0

    extract_s, embed_s = stage_seconds(marks)
    out = json.dumps(
        {
            "pipeline_s": pipeline_s,
            "extract_s": extract_s,
            "embed_s": embed_s,
            "files": len(marks),
            "per_file": file_seconds(marks),
        }
    )
    print(out)
    if timing_out:  # the monitor does not capture stdout, so the parent reads the timings here
        timing_out.write_text(out)


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m bench.index.run")
    ap.add_argument("--one", action="store_true")
    ap.add_argument("--stack")
    ap.add_argument("--stacks", nargs="+")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--no-power", action="store_true")
    ap.add_argument("--subject", required=True)
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--memray-out", type=Path)
    ap.add_argument("--timing-out", type=Path)
    ap.add_argument("--memray", action="store_true")
    ap.add_argument("--keep-homes", action="store_true")
    ap.add_argument("--timeout", type=float, help="ingestion.timeout_seconds for this run")
    args = ap.parse_args()
    args.input = args.input.expanduser()
    if not args.one and not args.stacks:
        ap.error("--stacks is required unless --one is given")
    if args.one:
        return _one(
            args.stack, args.subject, args.input, args.memray_out, args.timing_out, args.timeout
        )

    session = results.new_session()
    info = results.session_info(session, Path(sys.executable))
    timeout_s = ingestion_settings(args.timeout).timeout_seconds
    memray_dir = results.RESULTS_DIR / "memray"
    if args.memray:
        memray_dir.mkdir(parents=True, exist_ok=True)
    with power_session(args.no_power) as power:
        idle = power.idle_mw() if power else None
        for stack, i in schedule(args.stacks, args.runs):
            _run(args, stack, i + 1, session, info, timeout_s, memray_dir, power, idle)
    print(f"session: {session}")


def _run(args, stack, run_no, session, info, timeout_s, memray_dir, power, idle) -> None:
    home = scratch_home()
    base = ["-m", "bench.index.run", "--one", "--stack", stack]
    base += ["--subject", args.subject, "--input", str(args.input)]
    if args.timeout is not None:
        base += ["--timeout", str(args.timeout)]
    captures = {}
    if args.memray:
        stem = memray_dir / f"{session}-{stack}-{run_no}"
        captures = {"parent": Path(f"{stem}-parent.bin"), "worker": Path(f"{stem}-worker.bin")}
        base += ["--memray-out", str(captures["worker"])]
    timing = home / "timing.json"  # outside the subject dir, so it never reaches store stats
    base += ["--timing-out", str(timing)]
    try:
        env = {**os.environ, "GROUNDLY_HOME": str(home), "PYTHONPATH": str(ROOT)}
        # under memray, monitor prepends `python -m memray run --follow-fork -o …`
        cmd = base if args.memray else [sys.executable, *base]
        m = monitor.run(cmd, env=env, cwd=ROOT, memray_out=captures.get("parent"), power=power)
        db = home / args.subject / "store.db"
        stats = store_stats(home, args.subject) if db.exists() else None
        per_file = json.loads(timing.read_text()).get("per_file", []) if timing.exists() else []
        failed = stats["failed"] if stats else []
        run = {
            "session": session, "bench": "index", "arm": stack, "subject": args.subject,
            "run_no": run_no, "memray": args.memray, "timeout_s": timeout_s,
            "items": stats["chunks"] if stats else None, **monitor.run_columns(m, idle, power),
        }  # fmt: skip
        children = {
            "file": store.file_rows(per_file, failed),
            "artifact": memray_artifacts(captures),
            **m.samples,
        }
        store.save(info, run, children)
        print(
            f"{stack} run {run_no}: rc={m.returncode} wall {m.wall_s:.1f}s "
            f"chunks {run['items']} failed {len(failed)} timed out {timed_out(failed)}"
        )
    finally:
        if args.keep_homes:
            print(f"kept home: {home}")
        else:
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main()
