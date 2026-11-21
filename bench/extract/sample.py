"""CLI: census the indexed PDFs and write the sample manifest."""

import argparse
from collections import Counter
from pathlib import Path

from bench.common.corpus import census, sample, write_manifest


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m bench.extract.sample")
    ap.add_argument("--home", type=Path, default=Path("~/.groundly").expanduser())
    ap.add_argument("--out", type=Path, default=Path("bench/corpus/manifest.json"))
    ap.add_argument("--version", type=int, default=1)
    args = ap.parse_args()

    pdfs = [
        (d.name, f)
        for d in sorted(args.home.iterdir())
        if (d / "materials").is_dir()
        for f in sorted((d / "materials").rglob("*"))
        if f.suffix.lower() == ".pdf"
    ]
    rows = census(pdfs)
    counts: dict[str, Counter] = {}
    for r in rows:
        counts.setdefault(r["subject"], Counter())[(r["kind"], r["lang"])] += 1
    for subject, c in sorted(counts.items()):
        cells = ", ".join(f"{k}/{lang}={n}" for (k, lang), n in sorted(c.items()))
        print(f"{subject}: {sum(c.values())} pages ({cells})")
    pages = sample(rows)
    write_manifest(pages, args.out, args.version)
    print(f"wrote {len(pages)} pages to {args.out}")


if __name__ == "__main__":
    main()
