"""Interleaved index runs: each run indexes a subject end to end through Groundly's real
IngestionPipeline in a scratch GROUNDLY_HOME, with a stack module as the extraction worker.

The parent stays light (no Groundly models, no torch/mlx); every Groundly import lives in
`_one`, a child whose process tree the monitor measures with its worker."""

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from bench.common import monitor, results

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


def stage_seconds(marks: dict) -> tuple[float, float]:
    """Sum per-file extract and embed time from stage timestamps ("extracting", "embedding",
    "end"). A file that fails extraction never reaches "embedding"; its extract time runs to
    its terminal event, so a timeout still counts."""
    extract_s = embed_s = 0.0
    for m in marks.values():
        if "extracting" not in m:
            continue
        extract_s += m.get("embedding", m.get("end", m["extracting"])) - m["extracting"]
        if "embedding" in m and "end" in m:
            embed_s += m["end"] - m["embedding"]
    return extract_s, embed_s


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
        {"pipeline_s": pipeline_s, "extract_s": extract_s, "embed_s": embed_s, "files": len(marks)}
    )
    print(out)
    if timing_out:  # the monitor does not capture stdout, so the parent reads the timings here
        timing_out.write_text(out)


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m bench.index.run")
    ap.add_argument("--one", action="store_true")
    ap.add_argument("--stack")
    ap.add_argument("--stacks", nargs="+")
    ap.add_argument("--runs", type=int, default=1)
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
    sha = results._sh("git", "-C", str(ROOT), "rev-parse", "--short", "HEAD")
    memray_dir = results.RESULTS_DIR / "memray"
    if args.memray:
        memray_dir.mkdir(parents=True, exist_ok=True)
    for stack, i in schedule(args.stacks, args.runs):
        run = i + 1
        home = scratch_home()
        cmd = [sys.executable, "-m", "bench.index.run", "--one", "--stack", stack]
        cmd += ["--subject", args.subject, "--input", str(args.input)]
        if args.timeout is not None:
            cmd += ["--timeout", str(args.timeout)]
        if args.memray:
            cmd += ["--memray-out", str(memray_dir / f"{session}-{stack}-{run}.bin")]
        timing = home / "timing.json"  # outside the subject dir, so it never reaches store stats
        cmd += ["--timing-out", str(timing)]
        try:
            env = {**os.environ, "GROUNDLY_HOME": str(home), "PYTHONPATH": str(ROOT)}
            m = monitor.run(cmd, env=env, cwd=ROOT)
            stats = (
                store_stats(home, args.subject)
                if (home / args.subject / "store.db").exists()
                else None
            )
            metrics = {"wall_s": m.wall_s, "peak_rss_mb": m.peak_rss_mb}
            if timing.exists():
                metrics |= {k: v for k, v in json.loads(timing.read_text()).items() if k != "files"}
            if stats:
                metrics |= {
                    "chunks": stats["chunks"],
                    "materials": stats["materials"],
                    "failed_count": len(stats["failed"]),
                }
            results.append(
                "index",
                {
                    "bench": "index",
                    "session": session,
                    "arm": stack,
                    "sha": sha,
                    "machine": results.machine(),
                    "versions": results.versions(Path(sys.executable)),
                    "ocr_lang": None,
                    "timeout_s": ingestion_settings(args.timeout).timeout_seconds,
                    "manifest_version": None,
                    "run": run,
                    "memray": args.memray,
                    "returncode": m.returncode,
                    "metrics": metrics,
                    "failed": stats["failed"] if stats else [],
                },
            )
            print(f"{stack} run {run}: rc={m.returncode} {metrics}")
        finally:
            if args.keep_homes:
                print(f"kept home: {home}")
            else:
                shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main()
