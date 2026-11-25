"""Builds small text-layer PDFs with pypdfium2 for the extraction worker tests."""

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
    image: Image.Image | None = None  # the bitmap drawn there; a red square when None
    image_in_form: bool = False  # drawn at half size in a Form XObject scaled 2x onto the page
    rotation: int = 0  # the page's /Rotate


def _text(
    pdf: pdfium.PdfDocument, page: pdfium.PdfPage, text: str, size: float, x: float, y: float
) -> None:
    obj = raw.FPDFPageObj_NewTextObj(pdf.raw, b"Helvetica", ctypes.c_float(size))
    buf = ctypes.create_string_buffer((text + "\0").encode("utf-16-le"))
    raw.FPDFText_SetText(obj, ctypes.cast(buf, ctypes.POINTER(raw.FPDF_WCHAR)))
    raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, x, y)
    raw.FPDFPage_InsertObject(page.raw, obj)


def _image(pdf: pdfium.PdfDocument, page: pdfium.PdfPage, spec: Page, cover: float) -> None:
    scale = cover**0.5
    w_img, h_img = spec.width * scale, spec.height * scale
    img = pdfium.PdfImage.new(pdf)
    bitmap = spec.image or Image.new("RGB", (64, 64), "red")
    img.set_bitmap(pdfium.PdfBitmap.from_pil(bitmap))
    img.set_matrix(pdfium.PdfMatrix().scale(w_img, h_img).translate(spec.width - w_img, 0))
    page.insert_obj(img)


def _image_in_form(pdf: pdfium.PdfDocument, page: pdfium.PdfPage, spec: Page) -> None:
    src = pdfium.PdfDocument.new()
    src_page = src.new_page(spec.width, spec.height)
    _image(src, src_page, spec, spec.image_cover / 4)
    src_page.gen_content()
    xobject = raw.FPDF_NewXObjectFromPage(pdf.raw, src.raw, 0)
    form = raw.FPDF_NewFormObjectFromXObject(xobject)
    raw.FPDFPageObj_Transform(form, 2, 0, 0, 2, -spec.width, 0)  # 2x about bottom-right
    raw.FPDFPage_InsertObject(page.raw, form)
    raw.FPDF_CloseXObject(xobject)


def make_pdf(path: Path, pages: list[Page]) -> Path:
    pdf = pdfium.PdfDocument.new()
    for spec in pages:
        page = pdf.new_page(spec.width, spec.height)
        y = spec.height - 60
        for text, size in spec.lines:
            _text(pdf, page, text, size, 40, y)
            y -= 1.6 * size
        if spec.image_cover > 0:
            if spec.image_in_form:
                _image_in_form(pdf, page, spec)
            else:
                _image(pdf, page, spec, spec.image_cover)
        page.gen_content()
        page.set_rotation(spec.rotation)
    pdf.save(path)
    return path
