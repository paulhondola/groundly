"""CLI: census the indexed materials and write the sample manifest.

Version 2 keeps version 1's pages and adds hand-check cells, standalone images and
rendered text pages (see `sample_v2`)."""

import argparse
import json
import random
from collections import Counter
from pathlib import Path

from bench.common.corpus import (
    REF_CELL,
    census,
    image_census,
    sample,
    sample_v2,
    write_manifest,
)
from groundly.ingestion.formats import IMAGE_SUFFIXES

ROOT = Path(__file__).parents[2]


def _materials(home: Path, suffixes) -> list[tuple[str, Path]]:
    return [
        (d.name, f)
        for d in sorted(home.iterdir())
        if (d / "materials").is_dir()
        for f in sorted((d / "materials").rglob("*"))
        if f.is_file() and f.suffix.lower() in suffixes
    ]


def _image_rows(images: list[tuple[str, Path]], seed: int) -> list[dict]:
    """OCR images in seeded order until both language cells can fill: only REF_CELL per
    language are ever drawn, and a full census would OCR every image in the materials."""
    from bench.stacks import docling
    from groundly.ingestion import extract_worker

    def read(path, pages, lang):
        return docling.read(extract_worker._first_frame(path), pages, lang)

    order = list(images)
    random.Random(seed).shuffle(order)
    rows: list[dict] = []
    seen: set[str] = set()
    for i, (subject, path) in enumerate(order, 1):
        if all(sum(r["lang"] == lang for r in rows) >= REF_CELL for lang in ("ro", "en")):
            break
        try:
            (row,) = image_census([(subject, path)], read)
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - an unreadable image is skipped
            print(f"skip {path.name}: {exc}")
            continue
        if row["sha256"] not in seen:
            seen.add(row["sha256"])
            rows.append(row)
        print(f"image {i}/{len(order)}: {row['lang']}", flush=True)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m bench.extract.sample")
    ap.add_argument("--home", type=Path, default=Path("~/.groundly").expanduser())
    ap.add_argument("--out", type=Path, default=ROOT / "bench/corpus/manifest.json")
    ap.add_argument("--version", type=int, default=1)
    # repo-relative, so the manifest loads from any checkout; bench commands run from the root
    ap.add_argument("--rendered", type=Path, default=Path("bench/reference/rendered"))
    args = ap.parse_args()

    rows = census(_materials(args.home, {".pdf"}))
    counts: dict[str, Counter] = {}
    for r in rows:
        counts.setdefault(r["subject"], Counter())[(r["kind"], r["lang"])] += 1
    for subject, c in sorted(counts.items()):
        cells = ", ".join(f"{k}/{lang}={n}" for (k, lang), n in sorted(c.items()))
        print(f"{subject}: {sum(c.values())} pages ({cells})")
    if args.version == 1:
        pages = sample(rows)
        write_manifest(pages, args.out, 1)
        print(f"wrote {len(pages)} pages to {args.out}")
        return

    v1 = json.loads(args.out.read_text())
    if v1["version"] != 1:
        raise SystemExit(f"{args.out} is version {v1['version']}; v2 is built from version 1")
    images = _image_rows(_materials(args.home, IMAGE_SUFFIXES), seed=0)
    pages, short = sample_v2(v1, rows, images, args.rendered)
    write_manifest(pages, args.out, 2, short_cells=short)
    cells = Counter((p["kind"], p["lang"]) for p in pages if p["ref"])
    print(
        "reference cells:", ", ".join(f"{k}/{lang}={n}" for (k, lang), n in sorted(cells.items()))
    )
    print("rendered:", Counter(p["lang"] for p in pages if p["source"] == "rendered"))
    print(f"short cells: {short or 'none'}")
    print(f"wrote {len(pages)} pages to {args.out}")


if __name__ == "__main__":
    main()
