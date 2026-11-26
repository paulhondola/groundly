"""CLI: run one stack's page mode over the manifest, score it, write each run to the store.

Every page becomes a `page` row (time, cap, error, scores) and a `page_text` row; the stack's
own page file is a temp file, gone once the run is stored."""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from huggingface_hub.constants import HF_HUB_CACHE

from bench.common import monitor, results, store
from bench.common.corpus import load_manifest
from bench.common.sampler import power_session
from bench.extract.score import SCORER_VERSION, page_scores, score

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


def page_rows(manifest: dict, outputs: list[dict], reference_dir: Path) -> tuple[list, list]:
    """`page` rows (scored where a reference exists) and `page_text` rows, manifest order."""
    info = {(p["sha256"], p["page"]): p for p in manifest["pages"]}
    scores = {(s["sha256"], s["page"]): s for s in page_scores(manifest, outputs, reference_dir)}
    pages, texts = [], []
    for o in outputs:
        key = (o["sha256"], o["page"])
        p, s = info[key], scores.get(key, {})
        pages.append(
            {"sha256": o["sha256"], "page": o["page"], "kind": p.get("kind"), "lang": p.get("lang"),
             "ref": p.get("ref"), "ms": o["ms"], "capped": o.get("capped"), "error": o.get("error"),
             "f1": s.get("f1"), "precision": s.get("precision"), "recall": s.get("recall"),
             "bigram_f1": s.get("bigram_f1"), "diacritic_recall": s.get("diacritic_recall"),
             "scorer_version": SCORER_VERSION}
        )  # fmt: skip
        texts.append({"sha256": o["sha256"], "page": o["page"], "text": o.get("text") or ""})
    return pages, texts


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
    ap.add_argument(
        "--runs", type=int, default=1, help="accuracy is deterministic; more for latency"
    )
    ap.add_argument("--no-power", action="store_true")
    args = ap.parse_args()

    manifest = load_manifest(args.manifest)
    if args.only_ref:
        manifest["pages"] = [p for p in manifest["pages"] if p["ref"]]
    session = args.session or results.new_session()
    info = results.session_info(session, Path(sys.executable))
    with power_session(args.no_power) as power:
        idle = power.idle_mw() if power else None
        for run_no in range(1, args.runs + 1):
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "pages.jsonl"
                cmd = [sys.executable, "-m", f"bench.stacks.{args.stack}", "--pages"]
                cmd += [str(args.manifest), "--out", str(out)]
                cmd += ["--only-ref"] if args.only_ref else []
                cmd += ["--ocr-lang", args.ocr_lang] if args.ocr_lang else []
                env = {**os.environ, "PYTHONPATH": str(ROOT)}
                m = monitor.run(cmd, env=env, cwd=ROOT, power=power)
                outputs, meta = collect(manifest, out)
            pages, texts = page_rows(manifest, outputs, args.reference_dir)
            run = {
                "session": session, "bench": "extract", "arm": args.stack, "run_no": run_no,
                "load_s": meta.get("load_s"), "manifest_version": manifest["version"],
                "items": len(outputs), **monitor.run_columns(m, idle, power),
            }  # fmt: skip
            store.save(info, run, {"page": pages, "page_text": texts, **m.samples})
            summary = score(manifest, outputs, args.reference_dir)
            summary |= {"wall_s": m.wall_s, "model_disk_mb": model_disk_mb(args.stack)}
            print(f"{args.stack} run {run_no}: rc={m.returncode}")
            print(json.dumps(summary, indent=1))
    print(f"session: {session}")


if __name__ == "__main__":
    main()
