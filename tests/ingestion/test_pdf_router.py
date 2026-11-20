from __future__ import annotations

import pypdfium2 as pdfium
import pytest
from docling_core.types.doc import DocItemLabel

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


def _lines(tmp_path, lines):
    pdf = pdfium.PdfDocument(make_pdf(tmp_path / "d.pdf", [Page(lines=lines)]))
    return pdf_router.page_lines(pdf[0])


def test_page_lines_reads_text_and_sizes(tmp_path):
    got = _lines(tmp_path, [("Deadlock conditions", 32), ("Mutual exclusion is required.", 16)])
    assert [t for t, _ in got] == ["Deadlock conditions", "Mutual exclusion is required."]
    assert [s for _, s in got] == [pytest.approx(32, abs=0.5), pytest.approx(16, abs=0.5)]


def test_soft_hyphen_marker_is_dropped(tmp_path, monkeypatch):
    # Helvetica reads U+00AD back as 0xFF, so stand in for pdfium's 0x02 marker.
    real = pdf_router.raw.FPDFText_GetUnicode
    monkeypatch.setattr(
        pdf_router.raw,
        "FPDFText_GetUnicode",
        lambda tp, i: 0x02 if real(tp, i) == 0xFF else real(tp, i),
    )
    got = _lines(tmp_path, [("using only send\u00adreceive, BUT", 16)])
    assert got[0][0] == "using only sendreceive, BUT"


def test_split_title_takes_the_leading_large_run():
    lines = [
        ("Deadlock", 32),
        ("conditions", 30),
        ("Body text that dominates the page", 16),
        ("Big again", 32),
    ]
    assert pdf_router.split_title(lines) == (
        "Deadlock conditions",
        ["Body text that dominates the page", "Big again"],
    )


def test_split_title_needs_three_letters():
    lines = [("12", 32), ("Body text here", 16)]
    assert pdf_router.split_title(lines) == (None, ["12", "Body text here"])


def test_split_title_none_when_all_one_size():
    lines = [("a b c", 16), ("d e f", 16)]
    assert pdf_router.split_title(lines) == (None, ["a b c", "d e f"])


def _labelled(doc, label):
    return [t.text for t in doc.texts if t.label == label]


def test_code_line_with_hash_stays_verbatim_body():
    doc = pdf_router.text_page_document(3, 960, 540, "Includes", ["#include <stdio.h>"], None)
    assert _labelled(doc, DocItemLabel.TEXT) == ["#include <stdio.h>"]
    assert _labelled(doc, DocItemLabel.SECTION_HEADER) == ["Includes"]


def test_untitled_page_carries_the_earlier_title():
    doc = pdf_router.text_page_document(4, 960, 540, None, ["more detail"], carried="Deadlock")
    assert _labelled(doc, DocItemLabel.SECTION_HEADER) == ["Deadlock"]


def test_every_item_cites_its_page():
    doc = pdf_router.text_page_document(7, 960, 540, "T", ["a", "b"], None)
    assert {t.prov[0].page_no for t in doc.texts} == {7}
