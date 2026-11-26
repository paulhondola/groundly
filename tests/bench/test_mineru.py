import pytest
from docling_core.types.doc import DocItemLabel
from PIL import Image

from bench.stacks import caps, mineru
from bench.stacks.markdown_page import markdown_page
from tests.ingestion.pdf_fixtures import Page, make_pdf


class StubClient:
    """Returns one text block per call, naming the call's order; `blocks` overrides it."""

    def __init__(self, blocks=None, long_on=None):
        self.calls = 0
        self.blocks = blocks
        self.long_on = long_on

    def two_step_extract(self, image):
        assert image.mode == "RGB"
        self.calls += 1
        if self.blocks is not None:
            return self.blocks
        text = "word " * 10 if self.calls == self.long_on else f"call {self.calls}"
        return [{"type": "text", "content": text}]


@pytest.fixture
def stub(monkeypatch):
    def install(**kw):
        client = StubClient(**kw)
        monkeypatch.setattr(mineru, "_get_client", lambda: client)
        monkeypatch.setattr(mineru, "_token_count", lambda s: len(s.split()))
        caps.drain()
        return client

    return install


def pdf(tmp_path, n=3):
    return make_pdf(tmp_path / "d.pdf", [Page(lines=[(f"p{i}", 20)]) for i in range(n)])


def test_markdown_page_cites_its_page():
    doc = markdown_page("# T\n\nbody", 7)
    assert doc.texts and all(t.prov[0].page_no == 7 for t in doc.texts)
    headings = {DocItemLabel.TITLE, DocItemLabel.SECTION_HEADER}  # docling maps `#` to TITLE
    assert [t.text for t in doc.texts if t.label in headings] == ["T"]


def test_markdown_page_of_nothing_is_an_empty_page():
    doc = markdown_page("", 7)
    assert not doc.texts and 7 in doc.pages


def test_reads_requested_pdf_pages_in_order(tmp_path, stub):
    stub()
    assert [n for n, _ in mineru.read(pdf(tmp_path), [3, 1], None)] == [1, 3]


def test_reads_every_page_when_pages_is_none(tmp_path, stub):
    stub()
    got = [(n, doc.export_to_markdown()) for n, doc in mineru.read(pdf(tmp_path), None, None)]
    assert got == [(1, "call 1"), (2, "call 2"), (3, "call 3")]


def test_a_standalone_image_is_page_one(tmp_path, stub):
    stub()
    png = tmp_path / "s.png"
    Image.new("L", (32, 32)).save(png)
    assert [n for n, _ in mineru.read(png, None, None)] == [1]


def test_furniture_blocks_are_dropped_and_titles_become_headings(tmp_path, stub):
    stub(
        blocks=[
            {"type": "header", "content": "Running head"},
            {"type": "title", "content": "Graphs"},
            {"type": "text", "content": "A graph is a pair."},
        ]
    )
    ((_, doc),) = mineru.read(pdf(tmp_path, 1), None, None)
    md = doc.export_to_markdown()
    assert "# Graphs" in md and "A graph is a pair." in md and "Running head" not in md


def test_a_table_block_becomes_a_table_not_html_text(tmp_path, stub):
    html = "<table><tr><td>alpha</td><td>beta</td></tr><tr><td>gamma | x</td><td>delta</td></tr></table>"
    stub(blocks=[{"type": "table", "content": html}])
    ((_, doc),) = mineru.read(pdf(tmp_path, 1), None, None)
    md = doc.export_to_markdown()
    assert len(doc.tables) == 1
    assert "<t" not in md and "&lt;" not in md
    assert all(w in md for w in ("alpha", "beta", "gamma", "delta"))


def test_a_page_that_hits_the_token_cap_is_yielded_and_counted(tmp_path, stub, monkeypatch):
    monkeypatch.setattr(mineru, "MAX_TOKENS", 10)
    stub(long_on=2)
    assert [n for n, _ in mineru.read(pdf(tmp_path), None, None)] == [1, 2, 3]
    assert caps.drain() == {2}


@pytest.mark.slow
def test_mineru_reads_a_real_page(tmp_path):
    path = make_pdf(tmp_path / "r.pdf", [Page(lines=[("Graph theory basics", 32)])])
    ((n, doc),) = mineru.read(path, None, None)
    assert n == 1 and "Graph" in doc.export_to_markdown()
