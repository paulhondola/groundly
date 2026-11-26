from pathlib import Path
from types import SimpleNamespace

import pytest
from docling.datamodel.base_models import VlmStopReason

from bench.stacks import caps, granite
from tests.ingestion.pdf_fixtures import Page, make_pdf
from tests.ingestion.test_pages import _docling_doc


class StubConverter:
    """A VlmPipeline converter: `long_on` pages report a length stop."""

    def __init__(self, page_count, long_on=(), from_one=False):
        self.page_count = page_count
        self.from_one = from_one  # docling 2.115's VlmPipeline numbers a range's pages from 1
        self.long_on = set(long_on)
        self.calls: list[tuple[int, int]] = []

    def convert(self, path, page_range):
        self.calls.append(page_range)
        start, end = page_range
        nos = range(start, min(end, self.page_count) + 1)
        pages = [
            SimpleNamespace(
                page_no=n,
                predictions=SimpleNamespace(
                    vlm_response=SimpleNamespace(
                        stop_reason=VlmStopReason.LENGTH
                        if n in self.long_on
                        else VlmStopReason.UNSPECIFIED,
                        num_tokens=12,
                    )
                ),
            )
            for n in nos
        ]
        return SimpleNamespace(
            document=_docling_doc(range(1, len(nos) + 1) if self.from_one else nos),
            input=SimpleNamespace(page_count=self.page_count),
            pages=pages,
        )


@pytest.fixture
def install(monkeypatch):
    def _install(stub):
        monkeypatch.setattr(granite, "_converter", lambda: stub)
        caps.drain()
        return stub

    return _install


PDF = Path("lec.pdf")


def test_reads_every_page_in_page_range_batches(install):
    stub = install(StubConverter(20))
    assert [n for n, _ in granite.read(PDF, None, None)] == list(range(1, 21))
    assert stub.calls == [(1, 16), (17, 20)]


def test_requested_pages_are_read_in_contiguous_runs(install):
    stub = install(StubConverter(20))
    assert [n for n, _ in granite.read(PDF, [9, 5, 6], None)] == [5, 6, 9]
    assert stub.calls == [(5, 6), (9, 9)]


def test_a_page_stopped_by_the_token_limit_is_counted(install):
    install(StubConverter(4, long_on={3}))
    assert [n for n, _ in granite.read(PDF, None, None)] == [1, 2, 3, 4]
    assert caps.drain() == {3}


def test_a_range_numbered_from_one_maps_back_to_its_pages(install):
    install(StubConverter(20, from_one=True))
    got = [(n, doc.export_to_markdown()) for n, doc in granite.read(PDF, [5, 6, 18], None)]
    assert got == [(5, "read by docling 1"), (6, "read by docling 2"), (18, "read by docling 1")]


def test_ocr_lang_is_ignored_without_error(install):
    install(StubConverter(1))
    assert [n for n, _ in granite.read(PDF, None, "ro")] == [1]


@pytest.mark.slow
def test_granite_reads_a_real_page(tmp_path):
    lines = [[("Graph theory basics", 32)], [("Trees are acyclic graphs", 32)]]
    path = make_pdf(tmp_path / "r.pdf", [Page(lines=ln) for ln in lines])
    ((n, doc),) = granite.read(path, [2], None)
    assert n == 2 and "Trees" in doc.export_to_markdown()
