from __future__ import annotations

import pypdfium2 as pdfium
import pytest

from groundly.ingestion import pdf_router
from tests.ingestion.pdf_fixtures import Page, make_pdf

SENTENCE = [("Mutual exclusion and circular wait are required.", 16)]


@pytest.mark.parametrize(
    ("lines", "cover", "expected"),
    [
        (SENTENCE, 0.0, "text"),
        ([("Deadlock", 32)], 0.0, "blank"),
        (SENTENCE, 0.10, "mixed"),
        ([], 0.5, "image"),
        (SENTENCE, 0.04, "text"),  # below 5 %
    ],
)
def test_classify_page(tmp_path, lines, cover, expected):
    pdf = pdfium.PdfDocument(make_pdf(tmp_path / "d.pdf", [Page(lines=lines, image_cover=cover)]))
    assert pdf_router.classify_page(pdf[0]) == expected


def _deck(tmp_path, landscape: int, portrait: int):
    pages = [Page(lines=SENTENCE)] * landscape + [
        Page(width=540, height=960, lines=SENTENCE)
    ] * portrait
    return pdfium.PdfDocument(make_pdf(tmp_path / "d.pdf", pages))


def test_landscape_majority_is_a_slide_deck(tmp_path):
    assert pdf_router.is_slide_deck(_deck(tmp_path, 2, 1))


def test_portrait_majority_is_not_a_slide_deck(tmp_path):
    assert not pdf_router.is_slide_deck(_deck(tmp_path, 1, 2))


def test_exactly_half_landscape_is_not_a_slide_deck(tmp_path):
    assert not pdf_router.is_slide_deck(_deck(tmp_path, 1, 1))
