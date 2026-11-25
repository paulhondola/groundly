"""Shared entry for a stack module: Groundly's worker protocol plus a page mode.

A stack replaces only the formats it claims in SUFFIXES; every other file takes
Groundly's own path, so a stack run indexes a whole course the way `main` does."""

import argparse
import json
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

from groundly.ingestion import extract_worker


def handler(stack: ModuleType) -> extract_worker.Handler:
    def handle(
        in_path: Path, out_path: Path, ocr_lang: str | None, on_page: Callable[[int], None]
    ) -> None:
        if in_path.suffix.lower() not in stack.SUFFIXES:
            extract_worker._handle(in_path, out_path, ocr_lang, on_page)
            return
        result = stack.extract(in_path, ocr_lang)
        if not any(c["text"].strip() for c in result["chunks"]):
            sys.exit(extract_worker.EXIT_NO_TEXT)
        out_path.write_text(json.dumps(result))

    return handle


def _page_mode(stack: ModuleType, manifest: Path, out: Path, only_ref: bool, ocr_lang) -> None:
    files: dict[tuple[str, str], list[int]] = defaultdict(list)
    for p in json.loads(manifest.read_text())["pages"]:
        if p.get("ref") or not only_ref:
            files[(p["sha256"], p["path_hint"])].append(p["page"])
    warmed = False
    with out.open("w") as f:
        for (sha, hint), pages in files.items():
            path = Path(hint).expanduser()
            try:
                if not warmed:  # model load stays out of every timing
                    stack.page_texts(path, pages[:1], ocr_lang)
                    warmed = True  # a failed warm-up leaves the next file to do it
                start = time.perf_counter()
                texts = stack.page_texts(path, pages, ocr_lang)
                each = (time.perf_counter() - start) * 1000 / len(pages)
                per_page = getattr(stack, "last_ms", {})
                rows = [
                    {"text": texts[n], "ms": per_page.get(n, each), "error": None} for n in pages
                ]
            except Exception as exc:  # noqa: BLE001 - one bad file must not end the run
                err = f"{type(exc).__name__}: {exc}"
                rows = [{"text": "", "ms": 0.0, "error": err} for _ in pages]
            for n, row in zip(pages, rows):
                f.write(json.dumps({"sha256": sha, "page": n, **row}) + "\n")
            f.flush()


def main(stack: ModuleType) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--pages", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--only-ref", action="store_true")
    ap.add_argument("--ocr-lang")
    args = ap.parse_args()
    if args.serve:
        extract_worker.serve_stdio(handler(stack))
    else:
        _page_mode(stack, args.pages, args.out, args.only_ref, args.ocr_lang)
