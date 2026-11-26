"""CLI: for every reference page, both engines' drafts and the page image, the inputs
to a hand-checked reference. Everything lands under the gitignored reference dir."""

import argparse
import json
from pathlib import Path

from PIL import Image

from bench.common.corpus import render
from groundly.ingestion import extract_worker

ROOT = Path(__file__).parents[2]


def _image(row: dict, images: Path) -> None:
    if row["source"] == "pdf":
        render(row, images)
        return
    images.mkdir(parents=True, exist_ok=True)
    frame = extract_worker._first_frame(Path(row["path_hint"]).expanduser())
    with Image.open(frame) as img:
        img.convert("RGB").save(images / f"{row['sha256']}-{row['page']}.png")


def write_drafts(rows: list[dict], ref_dir: Path, readers: dict) -> None:
    refs = [r for r in rows if r["ref"]]
    for r in refs:
        _image(r, ref_dir / "images")
    drafts = ref_dir / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    for name, read in readers.items():  # one engine at a time keeps one model loaded
        for r in refs:
            path = Path(r["path_hint"]).expanduser()
            if r["source"] == "image":
                path = extract_worker._first_frame(path)
            pages = [r["page"]] if r["source"] == "pdf" else None
            md = "\n\n".join(doc.export_to_markdown() for _, doc in read(path, pages, None))
            (drafts / f"{r['sha256']}-{r['page']}.{name}.md").write_text(md, encoding="utf-8")
            print(f"{name}: {r['sha256'][:8]}-{r['page']}", flush=True)


def main() -> None:
    from bench.stacks import docling, mineru

    ap = argparse.ArgumentParser(prog="python -m bench.extract.drafts")
    ap.add_argument("--manifest", type=Path, default=ROOT / "bench/corpus/manifest.json")
    ap.add_argument("--reference-dir", type=Path, default=ROOT / "bench/reference")
    args = ap.parse_args()
    rows = json.loads(args.manifest.read_text())["pages"]
    write_drafts(rows, args.reference_dir, {"docling": docling.read, "mineru": mineru.read})


if __name__ == "__main__":
    main()
