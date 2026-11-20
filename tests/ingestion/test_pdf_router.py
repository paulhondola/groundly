from __future__ import annotations

import json
from types import SimpleNamespace

import pypdfium2 as pdfium
import pytest
from docling_core.types.doc import DocItemLabel

from groundly.ingestion import extract_worker, pdf_router
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


# --- extract: routing and assembly. chunk_document and the converter are stubbed, so no
# model loads; the fake chunker yields one chunk per non-empty doc.


def _fake_chunk_document(doc, page=None):
    texts = [t.text for t in doc.texts]
    if not texts:
        return []
    headers = [t.text for t in doc.texts if t.label == DocItemLabel.SECTION_HEADER]
    return [
        {
            "text": "\n".join(texts),
            "heading_path": headers[-1] if headers else None,
            "page": page,
            "token_count": 1,
        }
    ]


class _StubConverter:
    def __init__(self, docs=None, init_error=None):
        self.docs = docs or {}
        self.init_error = init_error
        self.calls = []

    def initialize_pipeline(self, fmt):
        if self.init_error:
            raise self.init_error

    def convert(self, path, page_range):
        self.calls.append(page_range)
        return SimpleNamespace(document=self.docs.get(page_range[0]) or _docling_doc(page_range[0]))


def _docling_doc(page_no, text="read by docling", header=None):
    from docling_core.types.doc import BoundingBox, DoclingDocument, ProvenanceItem, Size

    doc = DoclingDocument(name="stub")
    doc.add_page(page_no=page_no, size=Size(width=960, height=540))
    prov = ProvenanceItem(
        page_no=page_no, bbox=BoundingBox(l=0, t=540, r=960, b=0), charspan=(0, 0)
    )
    if header:
        doc.add_heading(header, level=1, prov=prov)
    if text:
        doc.add_text(DocItemLabel.TEXT, text, prov=prov)
    return doc


@pytest.fixture
def stubs(monkeypatch):
    monkeypatch.setattr(extract_worker, "chunk_document", _fake_chunk_document)

    def install(converter):
        monkeypatch.setattr(extract_worker, "_converter", lambda lang: converter)
        return converter

    return install


def _no_converter(lang):
    raise AssertionError("a text-only deck must not load docling")


def test_text_only_deck_never_calls_the_converter(tmp_path, monkeypatch, stubs):
    monkeypatch.setattr(extract_worker, "_converter", _no_converter)
    pdf = make_pdf(tmp_path / "d.pdf", [Page(lines=SENTENCE)] * 3)
    result = pdf_router.extract(pdf, None)
    assert result["pages"] == 3
    assert [c["page"] for c in result["chunks"]] == [1, 2, 3]


def test_untitled_page_inherits_previous_title(tmp_path, monkeypatch, stubs):
    monkeypatch.setattr(extract_worker, "_converter", _no_converter)
    pdf = make_pdf(
        tmp_path / "d.pdf",
        [Page(lines=[("Deadlock", 32), *SENTENCE]), Page(lines=SENTENCE)],
    )
    result = pdf_router.extract(pdf, None)
    assert [c["heading_path"] for c in result["chunks"]] == ["Deadlock", "Deadlock"]


def test_first_untitled_page_has_no_heading(tmp_path, monkeypatch, stubs):
    monkeypatch.setattr(extract_worker, "_converter", _no_converter)
    pdf = make_pdf(
        tmp_path / "d.pdf", [Page(lines=SENTENCE), Page(lines=[("Later", 32), *SENTENCE])]
    )
    result = pdf_router.extract(pdf, None)
    assert result["chunks"][0]["heading_path"] is None


def test_mixed_and_image_pages_go_to_docling_one_page_at_a_time(tmp_path, stubs):
    stub = stubs(_StubConverter())
    pdf = make_pdf(
        tmp_path / "d.pdf",
        [
            Page(lines=SENTENCE),
            Page(image_cover=0.5),
            Page(lines=SENTENCE, image_cover=0.10),
            Page(lines=SENTENCE),
        ],
    )
    result = pdf_router.extract(pdf, None)
    assert stub.calls == [(2, 2), (3, 3)]
    assert [c["page"] for c in result["chunks"]] == [1, 2, 3, 4]


def test_docling_page_title_carries_to_next_text_page(tmp_path, stubs):
    stubs(_StubConverter(docs={2: _docling_doc(2, header="Scheduling")}))
    pdf = make_pdf(
        tmp_path / "d.pdf",
        [Page(lines=[("Deadlock", 32), *SENTENCE]), Page(image_cover=0.5), Page(lines=SENTENCE)],
    )
    result = pdf_router.extract(pdf, None)
    assert result["chunks"][-1]["heading_path"] == "Scheduling"


def test_image_page_with_no_text_is_skipped(tmp_path, stubs):
    stubs(_StubConverter(docs={2: _docling_doc(2, text=None)}))
    pdf = make_pdf(tmp_path / "d.pdf", [Page(lines=SENTENCE), Page(image_cover=0.5)])
    result = pdf_router.extract(pdf, None)
    assert [c["page"] for c in result["chunks"]] == [1]


def test_model_load_failure_on_docling_page_is_retryable(tmp_path, stubs):
    stubs(_StubConverter(init_error=OSError("couldn't connect to huggingface.co")))
    pdf = make_pdf(tmp_path / "d.pdf", [Page(lines=SENTENCE), Page(image_cover=0.5)])
    with pytest.raises(SystemExit) as exc:
        pdf_router.extract(pdf, None)
    assert exc.value.code == extract_worker.EXIT_MODEL_UNAVAILABLE


def _record_docling(monkeypatch):
    recorded, sentinel = [], {"pages": None, "chunks": []}

    def fake(path, ocr_lang=None):
        recorded.append((path, ocr_lang))
        return sentinel

    monkeypatch.setattr(extract_worker, "_extract_docling", fake)
    return recorded, sentinel


def test_unopenable_pdf_falls_back_to_docling_whole(tmp_path, monkeypatch):
    recorded, sentinel = _record_docling(monkeypatch)
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 not really")
    assert pdf_router.extract(bad, "ro") is sentinel
    assert recorded == [(bad, "ro")]


def test_portrait_pdf_goes_to_docling_whole(tmp_path, monkeypatch):
    recorded, sentinel = _record_docling(monkeypatch)
    pdf = make_pdf(tmp_path / "d.pdf", [Page(width=540, height=960, lines=SENTENCE)] * 2)
    assert pdf_router.extract(pdf, None) is sentinel
    assert recorded == [(pdf, None)]


def test_handle_routes_pdf_to_the_router(tmp_path, monkeypatch):
    out = {"pages": 1, "chunks": [{"text": "x", "heading_path": None, "page": 1, "token_count": 1}]}
    monkeypatch.setattr(pdf_router, "extract", lambda path, lang: out)
    pdf = make_pdf(tmp_path / "d.pdf", [Page(lines=SENTENCE)])
    extract_worker._handle(pdf, tmp_path / "out.json", None)
    assert json.loads((tmp_path / "out.json").read_text()) == out
