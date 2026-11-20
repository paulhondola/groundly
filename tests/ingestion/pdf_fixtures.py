"""Builds small text-layer PDFs with pypdfium2 for the page-router tests."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image
from pypdfium2 import raw


@dataclass
class Page:
    width: float = 960
    height: float = 540
    lines: list[tuple[str, float]] = field(default_factory=list)  # (text, font size), top down
    image_cover: float = 0.0  # fraction of the page area, placed bottom-right


def _text(
    pdf: pdfium.PdfDocument, page: pdfium.PdfPage, text: str, size: float, x: float, y: float
) -> None:
    obj = raw.FPDFPageObj_NewTextObj(pdf.raw, b"Helvetica", ctypes.c_float(size))
    buf = ctypes.create_string_buffer((text + "\0").encode("utf-16-le"))
    raw.FPDFText_SetText(obj, ctypes.cast(buf, ctypes.POINTER(raw.FPDF_WCHAR)))
    raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, x, y)
    raw.FPDFPage_InsertObject(page.raw, obj)


def make_pdf(path: Path, pages: list[Page]) -> Path:
    pdf = pdfium.PdfDocument.new()
    for spec in pages:
        page = pdf.new_page(spec.width, spec.height)
        y = spec.height - 60
        for text, size in spec.lines:
            _text(pdf, page, text, size, 40, y)
            y -= 1.6 * size
        if spec.image_cover > 0:
            scale = spec.image_cover**0.5
            w_img, h_img = spec.width * scale, spec.height * scale
            img = pdfium.PdfImage.new(pdf)
            img.set_bitmap(pdfium.PdfBitmap.from_pil(Image.new("RGB", (64, 64), "red")))
            img.set_matrix(pdfium.PdfMatrix().scale(w_img, h_img).translate(spec.width - w_img, 0))
            page.insert_obj(img)
        page.gen_content()
    pdf.save(path)
    return path
