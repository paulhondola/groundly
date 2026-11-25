"""The page reader (`docling_pages`) and the page loop (`chunk_pages`). The converter is
stubbed, so no model loads."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from docling.datamodel.base_models import InputFormat
from docling_core.types.doc import (
    BoundingBox,
    DocItemLabel,
    DoclingDocument,
    ProvenanceItem,
    Size,
)

from groundly.ingestion import extract_worker


def _docling_doc(page_nos, text="read by docling", header=None) -> DoclingDocument:
    """One document holding `page_nos` (an int or an iterable), each page with an optional
    level-1 header followed by one text item, every item cited to its own page."""
    pages = [page_nos] if isinstance(page_nos, int) else list(page_nos)
    doc = DoclingDocument(name="stub")
    for n in pages:
        doc.add_page(page_no=n, size=Size(width=960, height=540))
        prov = ProvenanceItem(page_no=n, bbox=BoundingBox(l=0, t=540, r=960, b=0), charspan=(0, 0))
        if header:
            doc.add_heading(header, level=1, prov=prov)
        if text:
            doc.add_text(DocItemLabel.TEXT, f"{text} {n}", prov=prov)
    return doc


class _StubConverter:
    def __init__(self, page_count, init_error=None):
        self.page_count = page_count
        self.init_error = init_error
        self.calls: list[tuple[int, int]] = []
        self.formats: list[InputFormat] = []

    def initialize_pipeline(self, fmt):
        self.formats.append(fmt)
        if self.init_error:
            raise self.init_error

    def convert(self, path, page_range):
        self.calls.append(page_range)
        start, end = page_range
        doc = _docling_doc(range(start, min(end, self.page_count) + 1))
        return SimpleNamespace(document=doc, input=SimpleNamespace(page_count=self.page_count))


@pytest.fixture
def install(monkeypatch):
    langs: list[str | None] = []

    def _install(stub):
        def converter(lang):
            langs.append(lang)
            return stub

        monkeypatch.setattr(extract_worker, "_converter", converter)
        return stub

    _install.langs = langs
    return _install


PDF = Path("lec.pdf")


def test_reads_every_page_in_batches(install):
    stub = install(_StubConverter(20))
    assert [n for n, _ in extract_worker.docling_pages(PDF, None, None)] == list(range(1, 21))
    assert stub.calls == [(1, 16), (17, 20)]


def test_each_page_document_holds_only_its_page(install):
    install(_StubConverter(3))
    for n, doc in extract_worker.docling_pages(PDF, None, None):
        assert {t.prov[0].page_no for t in doc.texts} == {n}


def test_page_count_a_multiple_of_the_batch_makes_one_call(install):
    stub = install(_StubConverter(16))
    list(extract_worker.docling_pages(PDF, None, None))
    assert stub.calls == [(1, 16)]


def test_requested_pages_are_read_in_contiguous_runs(install):
    stub = install(_StubConverter(20))
    assert [n for n, _ in extract_worker.docling_pages(PDF, [9, 3, 4], None)] == [3, 4, 9]
    assert stub.calls == [(3, 4), (9, 9)]


def test_ocr_lang_reaches_the_converter(install):
    install(_StubConverter(2))
    list(extract_worker.docling_pages(PDF, None, "ro"))
    assert install.langs and set(install.langs) == {"ro"}


def test_model_load_failure_is_retryable(install):
    stub = install(_StubConverter(2, init_error=OSError("couldn't connect to huggingface.co")))
    with pytest.raises(SystemExit) as exc:
        list(extract_worker.docling_pages(PDF, None, None))
    assert exc.value.code == extract_worker.EXIT_MODEL_UNAVAILABLE
    assert stub.calls == []


def test_an_image_is_one_page_on_the_image_pipeline(install):
    stub = install(_StubConverter(1))
    assert [n for n, _ in extract_worker.docling_pages(Path("slide.png"), None, None)] == [1]
    assert stub.formats == [InputFormat.IMAGE]


def _fake_chunk_document(doc, page=None):
    """One chunk per document that has text; its heading is the last section header."""
    texts = [t.text for t in doc.texts]
    if not any(t.label == DocItemLabel.TEXT for t in doc.texts):
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


@pytest.fixture
def fake_chunker(monkeypatch):
    monkeypatch.setattr(extract_worker, "chunk_document", _fake_chunk_document)


def _headings(pages):
    return [c["heading_path"] for c in extract_worker.chunk_pages(pages)["chunks"]]


def test_untitled_page_carries_the_previous_heading(fake_chunker):
    pages = [(1, _docling_doc(1, header="Deadlock")), (2, _docling_doc(2))]
    assert _headings(pages) == ["Deadlock", "Deadlock"]


def test_first_untitled_page_has_no_heading(fake_chunker):
    pages = [(1, _docling_doc(1)), (2, _docling_doc(2, header="Later"))]
    assert _headings(pages) == [None, "Later"]


def test_a_page_with_its_own_heading_keeps_it(fake_chunker):
    pages = [(1, _docling_doc(1, header="Deadlock")), (2, _docling_doc(2, header="Scheduling"))]
    assert _headings(pages) == ["Deadlock", "Scheduling"]


def test_blank_page_gets_no_chunk_and_the_carry_continues(fake_chunker):
    pages = [
        (1, _docling_doc(1, header="Deadlock")),
        (2, _docling_doc(2, text=None)),
        (3, _docling_doc(3)),
    ]
    result = extract_worker.chunk_pages(pages)
    assert result["pages"] == 3
    assert [(c["page"], c["heading_path"]) for c in result["chunks"]] == [
        (1, "Deadlock"),
        (3, "Deadlock"),
    ]


def test_on_page_reports_every_page_in_order(fake_chunker):
    seen: list[int] = []
    pages = [(n, _docling_doc(n, text=None if n == 2 else "x")) for n in (1, 2, 3)]
    extract_worker.chunk_pages(pages, seen.append)
    assert seen == [1, 2, 3]


@pytest.mark.slow
def test_carried_heading_is_embedded_like_contextualize():
    pages = [
        (1, _docling_doc(1, text="Mutual exclusion is required.", header="Deadlock")),
        (2, _docling_doc(2, text="Circular wait occurs among processes.")),
    ]
    page2 = [c for c in extract_worker.chunk_pages(pages)["chunks"] if c["page"] == 2]
    assert page2[0]["heading_path"] == "Deadlock"
    assert page2[0]["text"].startswith("Deadlock\n")
