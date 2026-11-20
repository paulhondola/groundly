"""Per-page PDF routing, imported only inside the extraction worker process.

The router reads each page's text layer and image coverage with pypdfium2 and
classifies it, so pages that need no layout model or OCR skip docling.
IMG_COVER equals docling 2.115's ``bitmap_area_threshold``: any page docling
would OCR is never classed "text", so it keeps docling's route.
"""

from __future__ import annotations

from typing import Literal

import pypdfium2 as pdfium
from pypdfium2 import raw

MIN_CHARS = 40
IMG_COVER = 0.05

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
    has_text = len(page.get_textpage().get_text_range().strip()) >= MIN_CHARS
    has_image = _image_cover(page) >= IMG_COVER
    if has_text:
        return "mixed" if has_image else "text"
    return "image" if has_image else "blank"


def is_slide_deck(pdf: pdfium.PdfDocument) -> bool:
    """True when more than half the pages are landscape (width >= height)."""
    sizes = [page.get_size() for page in pdf]
    landscape = sum(1 for width, height in sizes if width >= height)
    return landscape * 2 > len(sizes)
