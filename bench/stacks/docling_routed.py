"""Groundly's page router: slide decks walked page by page, text pages from the text
layer, the rest through docling one page at a time."""

import sys
import time
from pathlib import Path

import pypdfium2 as pdfium

from bench.stacks import docling_standard
from groundly.ingestion import extract_worker, pdf_router

SUFFIXES = frozenset({".pdf"})
extract = pdf_router.extract
# Per-page conversion time of the last page_texts call, read by the page-mode runner.
last_ms: dict[int, float] = {}
_ready: set[str | None] = set()


def _init_pipeline(ocr_lang: str | None) -> None:
    """Loads docling's models once, as `pdf_router.extract` does before its first
    docling page, so no timed page includes the load."""
    from docling.datamodel.base_models import InputFormat

    if ocr_lang not in _ready:
        extract_worker._model_step(
            lambda: extract_worker._converter(ocr_lang).initialize_pipeline(InputFormat.PDF)
        )
        _ready.add(ocr_lang)


def page_texts(path: Path, pages: list[int], ocr_lang: str | None) -> dict[int, str]:
    """Walks pages exactly as `pdf_router.extract` does, the carried title included,
    and returns only the requested ones, each timed on its own."""
    last_ms.clear()
    try:
        pdf = pdfium.PdfDocument(path)
    except pdfium.PdfiumError:
        return docling_standard.page_texts(path, pages, ocr_lang)
    try:
        if not pdf_router.is_slide_deck(pdf):
            return docling_standard.page_texts(path, pages, ocr_lang)
        wanted = set(pages)
        texts: dict[int, str] = {}
        carried: str | None = None
        for n in range(1, max(wanted) + 1):
            page = pdf[n - 1]
            try:
                start = time.perf_counter()
                if pdf_router.classify_page(page) in ("text", "blank"):
                    lines = pdf_router.page_lines(page)
                    if not lines:
                        text = ""
                    else:
                        title, body = pdf_router.split_title(lines)
                        width, height = page.get_size()
                        doc = pdf_router.text_page_document(
                            n, width, height, title, body, carried if title is None else None
                        )
                        carried = title or carried
                        text = doc.export_to_markdown()
                else:
                    _init_pipeline(ocr_lang)
                    start = time.perf_counter()  # model load is never timed
                    doc = (
                        extract_worker._converter(ocr_lang)
                        .convert(path, page_range=(n, n))
                        .document
                    )
                    carried = pdf_router.last_heading(doc) or carried
                    text = doc.export_to_markdown()
            finally:
                page.close()
            if n in wanted:
                texts[n] = text
                last_ms[n] = (time.perf_counter() - start) * 1000
        return texts
    finally:
        pdf.close()


if __name__ == "__main__":
    from bench.stacks._worker import main

    main(sys.modules[__name__])
