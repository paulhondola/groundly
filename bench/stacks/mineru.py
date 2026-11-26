"""MinerU2.5 two-step extraction on MLX, one rendered page at a time.

Needs the optional `bench` extra. Each page's blocks are joined in reading order as
Markdown and become a one-page document, so every chunk cites its page."""

import sys
from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from bench.stacks import caps
from bench.stacks.markdown_page import markdown_page
from groundly.ingestion.formats import IMAGE_SUFFIXES

SUFFIXES = frozenset({".pdf"}) | IMAGE_SUFFIXES
MODEL = "opendatalab/MinerU2.5-Pro-2605-1.2B"
RENDER_SCALE = 2
MAX_TOKENS = 8192
# The MLX backend drops the finish reason, so a length stop is read back from the text:
# a block re-encoding to within this fraction of the cap stopped on it.
# ponytail: heuristic; a real slide block is a few hundred tokens, far below the line.
_CAP_FRACTION = 0.95
# Running page furniture, not course content.
_SKIPPED = frozenset({"header", "footer", "page_number", "aside_text"})
_HEADINGS = frozenset({"title", "doc_title", "paragraph_title"})
_client = None


def _get_client():
    global _client
    if _client is None:
        from dataclasses import replace

        from mineru_vl_utils import MinerUClient
        from mineru_vl_utils.mineru_client import _build_default_sampling_params

        # MinerU's own per-block-type defaults, each capped; a dict passed in replaces them
        params = {
            k: replace(v, max_new_tokens=MAX_TOKENS)
            for k, v in _build_default_sampling_params("mlx-engine").items()
        }
        _client = MinerUClient(
            backend="mlx-engine", model_path=MODEL, use_tqdm=False, sampling_params=params
        )
    return _client


def _token_count(text: str) -> int:
    return len(_get_client().client.processor.tokenizer.encode(text, add_special_tokens=False))


class _Cells(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            if not self.rows:
                self.rows.append([])
            self.rows[-1].append(" ".join("".join(self._cell).split()))
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _table_markdown(html: str) -> str:
    """MinerU's table HTML as a Markdown pipe table, so docling reads cells, not tag text.

    A literal `|` becomes `&#124;`: docling's Markdown reader splits cells on `\\|` too.
    Spans are not expanded: a row keeps the cells it has, padded to the widest row."""
    parser = _Cells()
    parser.feed(html)
    rows = [r for r in parser.rows if r]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    lines = [
        "| " + " | ".join(c.replace("|", "&#124;") for c in r + [""] * (width - len(r))) + " |"
        for r in rows
    ]
    lines.insert(1, "|" + " --- |" * width)
    return "\n".join(lines)


def _markdown(blocks) -> str:
    parts = []
    for block in blocks:
        text = (block.get("content") or "").strip()
        if not text or block["type"] in _SKIPPED:
            continue
        if block["type"] == "table" and "<t" in text:
            text = _table_markdown(text)
        parts.append(f"# {text}" if block["type"] in _HEADINGS else text)
    return "\n\n".join(parts)


def _page(image: Image.Image, n: int):
    blocks = _get_client().two_step_extract(image)
    if any(_token_count(b.get("content") or "") >= MAX_TOKENS * _CAP_FRACTION for b in blocks):
        caps.hit(n)
    return n, markdown_page(_markdown(blocks), n)


def read(path: Path, pages: list[int] | None, ocr_lang: str | None) -> Iterator:
    if path.suffix.lower() in IMAGE_SUFFIXES:
        with Image.open(path) as img:
            yield _page(img.convert("RGB"), 1)
        return
    pdf = pdfium.PdfDocument(path)
    try:
        for n in sorted(pages) if pages else range(1, len(pdf) + 1):
            page = pdf[n - 1]
            try:
                image = page.render(scale=RENDER_SCALE).to_pil().convert("RGB")
            finally:
                page.close()
            yield _page(image, n)
    finally:
        pdf.close()


if __name__ == "__main__":
    from bench.stacks._worker import main

    main(sys.modules[__name__])
