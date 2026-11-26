"""Shared entry for a stack module: Groundly's worker protocol plus a page mode, both
built from the stack's one reader.

A stack exports SUFFIXES and `read(path, pages, ocr_lang)`, which yields
(page_no, one-page DoclingDocument) as `extract_worker.docling_pages` does. It replaces
only the formats it claims; every other file takes Groundly's own path, so a stack run
indexes a whole course the way `main` does."""

import argparse
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

from bench.stacks import caps
from groundly.ingestion import extract_worker
from groundly.ingestion.formats import IMAGE_SUFFIXES


def _readable(path: Path) -> Path:
    # the pixel cap and first-frame cut guard every image before a reader sees it
    return extract_worker._first_frame(path) if path.suffix.lower() in IMAGE_SUFFIXES else path


def handler(stack: ModuleType) -> extract_worker.Handler:
    def handle(
        in_path: Path, out_path: Path, ocr_lang: str | None, on_page: Callable[[int], None]
    ) -> None:
        if in_path.suffix.lower() not in stack.SUFFIXES:
            extract_worker._handle(in_path, out_path, ocr_lang, on_page)
            return
        result = extract_worker.chunk_pages(stack.read(_readable(in_path), None, ocr_lang), on_page)
        if not any(c["text"].strip() for c in result["chunks"]):
            sys.exit(extract_worker.EXIT_NO_TEXT)
        out_path.write_text(json.dumps(result))

    return handle


def _page_mode(stack: ModuleType, manifest: Path, out: Path, only_ref: bool, ocr_lang) -> None:
    """One timed `read` per (file, kind), so a page's `ms` is its call's time over the
    call's page count. The first page read once untimed carries the model load (`load_s`)."""
    groups: dict[tuple[str, str, str], list[int]] = {}
    for p in json.loads(manifest.read_text())["pages"]:
        if p.get("ref") or not only_ref:
            groups.setdefault((p["sha256"], p["path_hint"], p["kind"]), []).append(p["page"])
    paths: dict[str, Path] = {}
    load_s: float | None = None
    with out.open("w") as f:
        for (sha, hint, _), pages in groups.items():
            try:
                if hint not in paths:
                    paths[hint] = _readable(Path(hint).expanduser())
                path = paths[hint]
                if load_s is None:  # a failed warm-up leaves the next group to do it
                    start = time.perf_counter()
                    list(stack.read(path, pages[:1], ocr_lang))
                    load_s = time.perf_counter() - start
                    caps.drain()
                start = time.perf_counter()
                docs = dict(stack.read(path, pages, ocr_lang))
                each = (time.perf_counter() - start) * 1000 / len(pages)
                capped = caps.drain()
                rows = [
                    {"text": docs[n].export_to_markdown(), "ms": each, "error": None}
                    if n in docs
                    else {"text": "", "ms": 0.0, "error": "page not returned"}
                    for n in pages
                ]
            except Exception as exc:  # noqa: BLE001 - one bad file must not end the run
                err = f"{type(exc).__name__}: {exc}"
                capped = caps.drain()
                rows = [{"text": "", "ms": 0.0, "error": err} for _ in pages]
            for n, row in zip(pages, rows):
                f.write(json.dumps({"sha256": sha, "page": n, **row, "capped": n in capped}) + "\n")
            f.flush()
        f.write(json.dumps({"meta": {"load_s": load_s}}) + "\n")


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
