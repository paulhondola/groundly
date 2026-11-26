"""CLI: run one stack's page mode over the manifest, score it, append an `extract` row.

Page text lives only in a temp file that is deleted afterwards."""

import argparse
import json
import os
import statistics
import sys
import tempfile
from pathlib import Path

from bench.common import monitor, results
from bench.common.corpus import load_manifest
from bench.extract.score import score

ROOT = Path(__file__).parents[2]


def collect(manifest: dict, out_path: Path) -> tuple[list[dict], dict]:
    """The stack's page rows in manifest order, and its closing meta row."""
    seen, meta = {}, {}
    if out_path.is_file():
        for line in out_path.read_text().splitlines():
            row = json.loads(line)
            if "meta" in row:
                meta = row["meta"]
            else:
                seen[(row["sha256"], row["page"])] = row
    rows = [
        seen.get(
            (p["sha256"], p["page"]),
            {
                "sha256": p["sha256"],
                "page": p["page"],
                "text": "",
                "ms": 0.0,
                "error": "missing from stack output",
                "capped": False,
            },
        )
        for p in manifest["pages"]
    ]
    return rows, meta


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m bench.extract.run")
    ap.add_argument("--stack", required=True)
    ap.add_argument("--manifest", type=Path, default=ROOT / "bench/corpus/manifest.json")
    ap.add_argument("--reference-dir", type=Path, default=ROOT / "bench/reference")
    ap.add_argument("--only-ref", action="store_true")
    ap.add_argument("--ocr-lang")
    ap.add_argument("--session", default=None)
    args = ap.parse_args()

    manifest = load_manifest(args.manifest)
    if args.only_ref:
        manifest["pages"] = [p for p in manifest["pages"] if p["ref"]]
    fd, tmp = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    out = Path(tmp)
    try:
        cmd = [sys.executable, "-m", f"bench.stacks.{args.stack}", "--pages", str(args.manifest)]
        cmd += ["--out", tmp] + (["--only-ref"] if args.only_ref else [])
        cmd += ["--ocr-lang", args.ocr_lang] if args.ocr_lang else []
        m = monitor.run(cmd, env={**os.environ, "PYTHONPATH": str(ROOT)}, cwd=ROOT)
        outputs, meta = collect(manifest, out)
    finally:
        out.unlink(missing_ok=True)

    metrics = score(manifest, outputs, args.reference_dir)
    timed = [o["ms"] for o in outputs if not o["error"]]
    metrics |= {
        "wall_s": m.wall_s,
        "peak_rss_mb": m.peak_rss_mb,
        "ms_p50": statistics.median(timed) if timed else None,
        "load_s": meta.get("load_s"),
        "capped_pages": sum(1 for o in outputs if o.get("capped")),
    }
    session = args.session or results.new_session()
    sha = results._sh("git", "-C", str(ROOT), "rev-parse", "--short", "HEAD")
    results.append(
        "extract",
        {
            "bench": "extract",
            "session": session,
            "arm": args.stack,
            "sha": sha,
            "machine": results.machine(),
            "versions": results.versions(Path(sys.executable)),
            "ocr_lang": args.ocr_lang,
            "manifest_version": manifest["version"],
            "run": 1,
            "returncode": m.returncode,
            "metrics": metrics,
        },
    )
    print(json.dumps(metrics, indent=1))
    print(f"session: {session}")


if __name__ == "__main__":
    main()
