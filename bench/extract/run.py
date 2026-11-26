"""CLI: run one stack's page mode over the manifest, score it, append an `extract` row.

Page rows (text, ms, capped, error) stay in bench/results/pages/<session>-<arm>.jsonl,
gitignored like every result, so per-page figures can be drawn later."""

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

from huggingface_hub.constants import HF_HUB_CACHE

from bench.common import monitor, results
from bench.common.corpus import load_manifest
from bench.extract.score import score

ROOT = Path(__file__).parents[2]
_DOCLING = ("docling-project/docling-layout-heron", "docling-project/docling-models")
_MINERU = "opendatalab/MinerU2.5-Pro-2605-1.2B"
_GRANITE = "ibm-granite/granite-docling-258M-mlx"
# HF repos each arm reads; an arm that runs docling also reads RapidOCR's bundled models.
MODEL_REPOS: dict[str, tuple[str, ...]] = {
    "docling": _DOCLING,
    "product": _DOCLING,
    "mineru": (_MINERU,),
    "granite": (_GRANITE,),
    "docling_mineru": (*_DOCLING, _MINERU),
    "docling_granite": (*_DOCLING, _GRANITE),
}


def _tree_bytes(root: Path) -> int:
    # snapshot files are links into a shared blob store: size the targets, each once
    blobs = {f.resolve() for f in root.rglob("*") if f.is_file()}
    return sum(b.stat().st_size for b in blobs)


def model_disk_mb(arm: str, cache: Path = Path(HF_HUB_CACHE)) -> float | None:
    """On-disk size of the models an arm reads; None when the arm or a repo is unknown here."""
    repos = MODEL_REPOS.get(arm)
    dirs = [cache / f"models--{r.replace('/', '--')}" / "snapshots" for r in repos or ()]
    if not repos or not all(d.is_dir() for d in dirs):
        return None
    total = sum(_tree_bytes(d) for d in dirs)
    if _DOCLING[0] in repos:
        import rapidocr

        total += _tree_bytes(Path(rapidocr.__file__).parent / "models")
    return total / (1 << 20)


def pages_path(session: str, arm: str, results_dir: Path = results.RESULTS_DIR) -> Path:
    """Where one arm's page rows from one session are kept."""
    return results_dir / "pages" / f"{session}-{arm}.jsonl"


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
    session = args.session or results.new_session()
    out = pages_path(session, args.stack)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", f"bench.stacks.{args.stack}", "--pages", str(args.manifest)]
    cmd += ["--out", str(out)] + (["--only-ref"] if args.only_ref else [])
    cmd += ["--ocr-lang", args.ocr_lang] if args.ocr_lang else []
    m = monitor.run(cmd, env={**os.environ, "PYTHONPATH": str(ROOT)}, cwd=ROOT)
    outputs, meta = collect(manifest, out)

    metrics = score(manifest, outputs, args.reference_dir)
    timed = [o["ms"] for o in outputs if not o["error"]]
    metrics |= {
        "wall_s": m.wall_s,
        "peak_rss_mb": m.peak_rss_mb,
        "ms_p50": statistics.median(timed) if timed else None,
        "load_s": meta.get("load_s"),
        "capped_pages": sum(1 for o in outputs if o.get("capped")),
        "model_disk_mb": model_disk_mb(args.stack),
    }
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
