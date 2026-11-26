"""A reader that sends each page to a reader picked by its text-layer class, so a VLM
reads only the pages docling would have to OCR whole."""

from collections.abc import Callable, Iterator
from pathlib import Path

import pypdfium2 as pdfium

from bench.stacks.text_layer import classify_page
from groundly.ingestion.formats import IMAGE_SUFFIXES

Reader = Callable[[Path, list[int] | None, str | None], Iterator]


def _classes(path: Path, pages: list[int] | None) -> list[tuple[int, str]]:
    if path.suffix.lower() in IMAGE_SUFFIXES:
        return [(1, "image")]
    pdf = pdfium.PdfDocument(path)
    try:
        out = []
        for n in sorted(set(pages)) if pages else range(1, len(pdf) + 1):
            page = pdf[n - 1]
            try:
                out.append((n, classify_page(page)))
            finally:
                page.close()
        return out
    finally:
        pdf.close()


def by_kind(readers: dict[str, Reader], default: Reader) -> Reader:
    def read(path: Path, pages: list[int] | None, ocr_lang: str | None) -> Iterator:
        # contiguous runs that share a reader keep pages ascending without holding any back
        runs: list[tuple[Reader, list[int]]] = []
        for n, kind in _classes(path, pages):
            r = readers.get(kind, default)
            if runs and runs[-1][0] is r and runs[-1][1][-1] == n - 1:
                runs[-1][1].append(n)
            else:
                runs.append((r, [n]))
        for r, run in runs:
            yield from r(path, run, ocr_lang)

    return read
