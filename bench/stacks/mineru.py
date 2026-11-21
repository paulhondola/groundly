"""PDFs through MinerU2.5 two-step extraction on MLX, one rendered page at a time.

Needs the optional `bench` extra. Each page's blocks are joined in reading order as
Markdown; `extract` turns that Markdown into a one-page document so every chunk cites
its page."""

import sys
import time
from io import BytesIO
from pathlib import Path

import pypdfium2 as pdfium

from groundly.ingestion import extract_worker

SUFFIXES = frozenset({".pdf"})
MODEL = "opendatalab/MinerU2.5-Pro-2605-1.2B"
RENDER_SCALE = 2
# Running page furniture, not course content.
_SKIPPED = frozenset({"header", "footer", "page_number", "aside_text"})
_HEADINGS = frozenset({"title", "doc_title", "paragraph_title"})
# Per-page extraction time of the last page_texts call, read by the page-mode runner.
last_ms: dict[int, float] = {}
_client = None


def _get_client():
    global _client
    if _client is None:
        from mineru_vl_utils import MinerUClient

        _client = MinerUClient(backend="mlx-engine", model_path=MODEL, use_tqdm=False)
    return _client


def _markdown(blocks) -> str:
    parts = []
    for block in blocks:
        text = (block.get("content") or "").strip()
        if not text or block["type"] in _SKIPPED:
            continue
        parts.append(f"# {text}" if block["type"] in _HEADINGS else text)
    return "\n\n".join(parts)


def _page_markdown(pdf: pdfium.PdfDocument, n: int) -> str:
    page = pdf[n - 1]
    try:
        image = page.render(scale=RENDER_SCALE).to_pil().convert("RGB")
    finally:
        page.close()
    return _markdown(_get_client().two_step_extract(image))


def page_texts(path: Path, pages: list[int], ocr_lang: str | None) -> dict[int, str]:
    last_ms.clear()
    pdf = pdfium.PdfDocument(path)
    try:
        texts = {}
        for n in pages:
            start = time.perf_counter()
            texts[n] = _page_markdown(pdf, n)
            last_ms[n] = (time.perf_counter() - start) * 1000
        return texts
    finally:
        pdf.close()


def _markdown_document(markdown: str, n: int):
    from docling.datamodel.base_models import DocumentStream, InputFormat
    from docling.document_converter import DocumentConverter

    stream = DocumentStream(name=f"page-{n}.md", stream=BytesIO(markdown.encode()))
    return DocumentConverter(allowed_formats=[InputFormat.MD]).convert(stream).document


def extract(path: Path, ocr_lang: str | None) -> dict:
    pdf = pdfium.PdfDocument(path)
    try:
        chunks = []
        for n in range(1, len(pdf) + 1):
            markdown = _page_markdown(pdf, n)
            if markdown.strip():
                chunks += extract_worker.chunk_document(_markdown_document(markdown, n), page=n)
        return {"pages": len(pdf), "chunks": chunks}
    finally:
        pdf.close()


if __name__ == "__main__":
    from bench.stacks._worker import main

    main(sys.modules[__name__])
