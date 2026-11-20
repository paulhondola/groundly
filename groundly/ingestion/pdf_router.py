"""Per-page PDF routing, imported only inside the extraction worker process.

The router reads each page's text layer and image coverage with pypdfium2 and
classifies it, so pages that need no layout model or OCR skip docling.
IMG_COVER equals docling 2.115's ``bitmap_area_threshold``: any page docling
would OCR is never classed "text", so it keeps docling's route.
"""

from __future__ import annotations

from collections import Counter
from statistics import median
from typing import TYPE_CHECKING, Literal

import pypdfium2 as pdfium
from pypdfium2 import raw

MIN_CHARS = 40
IMG_COVER = 0.05

if TYPE_CHECKING:
    from docling_core.types.doc import DoclingDocument

TITLE_RATIO = 1.15
TITLE_MIN_LETTERS = 3
# pdfium's soft-hyphen marker (0x02 / U+FFFE) and U+00AD: docling drops them too.
_DROPPED = {0x02, 0xFFFE, 0x00AD}

PageClass = Literal["text", "blank", "mixed", "image"]


def _image_cover(page: pdfium.PdfPage) -> float:
    """Fraction of the page area covered by images, clipped to the page box."""
    width, height = page.get_size()
    covered = 0.0
    for obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE]):
        left, bottom, right, top = obj.get_bounds()
        covered += max(0.0, min(right, width) - max(left, 0.0)) * max(
            0.0, min(top, height) - max(bottom, 0.0)
        )
    return min(1.0, covered / (width * height))


def classify_page(page: pdfium.PdfPage) -> PageClass:
    """ "text": enough text, little image. "mixed": enough text and images.
    "image": images and no real text. "blank": neither."""
    tp = page.get_textpage()
    try:
        has_text = len(tp.get_text_range().strip()) >= MIN_CHARS
    finally:
        tp.close()
    has_image = _image_cover(page) >= IMG_COVER
    if has_text:
        return "mixed" if has_image else "text"
    return "image" if has_image else "blank"


def is_slide_deck(pdf: pdfium.PdfDocument) -> bool:
    """True when more than half the pages are landscape (width >= height)."""
    sizes = [page.get_size() for page in pdf]
    landscape = sum(1 for width, height in sizes if width >= height)
    return landscape * 2 > len(sizes)


def page_lines(page: pdfium.PdfPage) -> list[tuple[str, float]]:
    """Non-empty stripped visual lines, each with its median font size.

    Spaces are left out of the size (pdfium reports 1 for them)."""
    tp = page.get_textpage()
    try:
        lines: list[tuple[str, float]] = []
        chars: list[str] = []
        sizes: list[float] = []

        def flush() -> None:
            text = "".join(chars).strip()
            if text and sizes:
                lines.append((text, float(median(sizes))))
            chars.clear()
            sizes.clear()

        for i in range(tp.count_chars()):
            code = raw.FPDFText_GetUnicode(tp.raw, i)
            if code in (0x0D, 0x0A):
                flush()
            elif code not in _DROPPED:
                char = chr(code)
                chars.append(char)
                if not char.isspace():
                    sizes.append(raw.FPDFText_GetFontSize(tp.raw, i))
        flush()
        return lines
    finally:
        tp.close()


def split_title(lines: list[tuple[str, float]]) -> tuple[str | None, list[str]]:
    """The slide title is the leading run of lines clearly larger than the body.

    Body size is the character-weighted modal size; a title line needs
    TITLE_MIN_LETTERS letters. Nothing after the first body line is a title."""
    weight: Counter[int] = Counter()
    for text, size in lines:
        weight[round(size)] += len(text)
    body_size = weight.most_common(1)[0][0] if weight else 0
    n = 0
    for text, size in lines:
        letters = sum(c.isalpha() for c in text)
        if size < TITLE_RATIO * body_size or letters < TITLE_MIN_LETTERS:
            break
        n += 1
    title = " ".join(t for t, _ in lines[:n]) if n else None
    return title, [t for t, _ in lines[n:]]


def text_page_document(
    page_no: int,
    width: float,
    height: float,
    title: str | None,
    body: list[str],
    carried: str | None,
) -> DoclingDocument:
    """A one-page document: the title (else the carried one) as a heading, body lines
    verbatim as text. Every item cites `page_no` through a whole-page box."""
    from docling_core.types.doc import (
        BoundingBox,
        DocItemLabel,
        DoclingDocument,
        ProvenanceItem,
        Size,
    )

    doc = DoclingDocument(name=f"page-{page_no}")
    doc.add_page(page_no=page_no, size=Size(width=width, height=height))
    prov = ProvenanceItem(
        page_no=page_no,
        bbox=BoundingBox(l=0, t=height, r=width, b=0),
        charspan=(0, 0),
    )
    heading = title or carried
    if heading:
        doc.add_heading(heading, level=1, prov=prov)
    for line in body:
        doc.add_text(DocItemLabel.TEXT, line, prov=prov)
    return doc
