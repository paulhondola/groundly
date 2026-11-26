import itertools
import json
import sys
import types
from pathlib import Path

import pytest
from docling_core.types.doc import (
    BoundingBox,
    DocItemLabel,
    DoclingDocument,
    ProvenanceItem,
    Size,
)
from PIL import Image

from bench.stacks import _worker, caps
from bench.stacks._worker import handler
from groundly.ingestion import extract_worker


def page_doc(n: int, text: str | None = "page text") -> DoclingDocument:
    doc = DoclingDocument(name="stub")
    doc.add_page(page_no=n, size=Size(width=960, height=540))
    if text:
        prov = ProvenanceItem(page_no=n, bbox=BoundingBox(l=0, t=540, r=960, b=0), charspan=(0, 0))
        doc.add_text(DocItemLabel.TEXT, f"{text} {n}", prov=prov)
    return doc


def fake_stack(read=None, suffixes=frozenset({".pdf", ".tif", ".tiff", ".png"})):
    stack = types.ModuleType("fake")
    stack.SUFFIXES = suffixes
    stack.calls = []

    def default_read(path, pages, ocr_lang):
        stack.calls.append((path, pages))
        for n in pages or [1, 2]:
            yield n, page_doc(n)

    stack.read = read or default_read
    return stack


@pytest.fixture
def fake_chunker(monkeypatch):
    def chunk_document(doc, page=None):
        if not doc.texts:
            return []
        text = "\n".join(t.text for t in doc.texts)
        return [{"text": text, "heading_path": None, "page": page, "token_count": 1}]

    monkeypatch.setattr(extract_worker, "chunk_document", chunk_document)


def touch(path: Path) -> Path:
    path.write_bytes(b"x")
    return path


def tiff(path: Path, frames: int = 3) -> Path:
    imgs = [Image.new("RGB", (8, 8), (i * 40, 0, 0)) for i in range(frames)]
    imgs[0].save(path, save_all=True, append_images=imgs[1:])
    return path


def test_serve_sends_claimed_suffixes_through_chunk_pages(tmp_path, fake_chunker):
    seen: list[int] = []
    handler(fake_stack())(touch(tmp_path / "a.pdf"), tmp_path / "out.json", None, seen.append)
    chunks = json.loads((tmp_path / "out.json").read_text())["chunks"]
    assert [c["page"] for c in chunks] == [1, 2]
    assert seen == [1, 2]


def test_serve_cuts_an_image_to_its_first_frame_before_read(tmp_path, fake_chunker):
    stack = fake_stack()
    handler(stack)(tiff(tmp_path / "scan.tif"), tmp_path / "out.json", None, print)
    ((path, _),) = stack.calls
    assert Image.open(path).n_frames == 1


def test_serve_exits_no_text_when_read_yields_nothing(tmp_path, fake_chunker):
    stack = fake_stack(read=lambda path, pages, lang: iter([(1, page_doc(1, text=None))]))
    with pytest.raises(SystemExit) as e:
        handler(stack)(touch(tmp_path / "a.pdf"), tmp_path / "out.json", None, print)
    assert e.value.code == extract_worker.EXIT_NO_TEXT
    assert not (tmp_path / "out.json").exists()


def test_other_suffixes_use_groundly_default(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(extract_worker, "_handle", lambda *a: seen.append(a))
    stack = fake_stack()
    pages: list[int] = []
    handler(stack)(tmp_path / "n.txt", tmp_path / "out.json", "ro", pages.append)
    assert stack.calls == []
    assert seen == [(tmp_path / "n.txt", tmp_path / "out.json", "ro", pages.append)]


def _page_mode(tmp_path, monkeypatch, stack, rows, *flags):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"version": 2, "pages": rows}))
    out = tmp_path / "o.jsonl"
    monkeypatch.setattr(sys, "argv", ["x", "--pages", str(manifest), "--out", str(out), *flags])
    _worker.main(stack)
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    return [r for r in lines if "meta" not in r], [r["meta"] for r in lines if "meta" in r]


def row(path, n, kind="text", ref=False, sha="s"):
    return {"sha256": sha, "page": n, "path_hint": str(path), "kind": kind, "ref": ref}


def test_page_mode_times_one_call_per_file_and_kind(tmp_path, monkeypatch):
    ticks = itertools.count()
    monkeypatch.setattr(_worker.time, "perf_counter", lambda: float(next(ticks)))
    stack = fake_stack()
    pdf = touch(tmp_path / "d.pdf")
    rows, meta = _page_mode(
        tmp_path, monkeypatch, stack, [row(pdf, 1), row(pdf, 2), row(pdf, 3, kind="image")]
    )
    assert [pages for _, pages in stack.calls] == [[1], [1, 2], [3]]
    assert [(r["page"], r["ms"]) for r in rows] == [(1, 500.0), (2, 500.0), (3, 1000.0)]
    assert [r["text"] for r in rows] == ["page text 1", "page text 2", "page text 3"]
    assert meta == [{"load_s": 1.0}]


def test_page_mode_reads_an_image_as_frame_zero(tmp_path, monkeypatch):
    stack = fake_stack()
    _page_mode(tmp_path, monkeypatch, stack, [row(tiff(tmp_path / "s.tif"), 1, kind="standalone")])
    assert all(Image.open(path).n_frames == 1 for path, _ in stack.calls)


def test_page_mode_marks_capped_pages(tmp_path, monkeypatch):
    def read(path, pages, lang):
        for n in pages:
            if n == 2:
                caps.hit(2)
            yield n, page_doc(n)

    pdf = touch(tmp_path / "d.pdf")
    rows, _ = _page_mode(tmp_path, monkeypatch, fake_stack(read=read), [row(pdf, 1), row(pdf, 2)])
    assert [(r["page"], r["capped"]) for r in rows] == [(1, False), (2, True)]


def test_page_mode_writes_one_line_per_page_and_isolates_file_errors(tmp_path, monkeypatch):
    good, bad = touch(tmp_path / "good.pdf"), touch(tmp_path / "bad.pdf")

    def read(path, pages, lang):
        if path == bad:
            raise ValueError("boom")
        for n in pages:
            yield n, page_doc(n)

    stack = fake_stack(read=read)
    rows = [row(good, 1, sha="g"), row(good, 2, ref=True, sha="g"), row(bad, 1, sha="b")]
    got, _ = _page_mode(tmp_path, monkeypatch, stack, rows, "--only-ref")
    assert [(r["sha256"], r["page"], r["text"]) for r in got] == [("g", 2, "page text 2")]
    got, _ = _page_mode(tmp_path, monkeypatch, stack, rows)
    assert [(r["sha256"], r["page"], r["error"]) for r in got] == [
        ("g", 1, None),
        ("g", 2, None),
        ("b", 1, "ValueError: boom"),
    ]


def test_a_page_the_reader_skips_is_an_error(tmp_path, monkeypatch):
    stack = fake_stack(read=lambda path, pages, lang: iter([(1, page_doc(1))]))
    pdf = touch(tmp_path / "d.pdf")
    rows, _ = _page_mode(tmp_path, monkeypatch, stack, [row(pdf, 1), row(pdf, 2)])
    assert [r["error"] for r in rows] == [None, "page not returned"]
