"""Every PDF through docling whole, as Groundly does without the page router."""

import sys
from pathlib import Path

from groundly.ingestion import extract_worker

SUFFIXES = frozenset({".pdf"})
extract = extract_worker._extract_docling


def page_texts(path: Path, pages: list[int], ocr_lang: str | None) -> dict[int, str]:
    """The whole file converts once, so the caller's `ms` is that time divided by the
    requested page count: an amortized figure, not a per-page one."""
    doc = extract_worker._converter(ocr_lang).convert(path).document
    return {n: doc.export_to_markdown(page_no=n) for n in pages}


if __name__ == "__main__":
    from bench.stacks._worker import main

    main(sys.modules[__name__])
