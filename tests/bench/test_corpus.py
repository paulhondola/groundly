import ctypes
import itertools
import json
import subprocess
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from docling_core.types.doc import DocItemLabel, DoclingDocument
from PIL import Image
from pypdfium2 import raw

from bench.common.corpus import (
    census,
    image_census,
    load_manifest,
    page_lang,
    sample,
    sample_v2,
    write_manifest,
)
from tests.ingestion import pdf_fixtures


def make_pdf(
    path: Path, text: str = "Hello world this is a page with enough text to count"
) -> Path:
    pdf = pdfium.PdfDocument.new()
    page = pdf.new_page(595, 842)
    font = raw.FPDFText_LoadStandardFont(pdf, b"Helvetica")
    obj = raw.FPDFPageObj_CreateTextObj(pdf, font, 12)
    units = (ctypes.c_ushort * (len(text) + 1))(*text.encode("utf-16-le")[::2], 0)
    raw.FPDFText_SetText(obj, units)
    raw.FPDFPage_InsertObject(page, obj)
    raw.FPDFPage_GenerateContent(page)
    pdf.save(path)
    return path


def synthetic_census() -> list[dict]:
    rows = []
    for kind, lang, deck in itertools.product(
        ("text", "blank", "mixed", "image"), ("ro", "en"), (False, True)
    ):
        for page in range(50):
            rows.append(
                {
                    "subject": "s",
                    "sha256": f"{kind}{lang}{deck}",
                    "path_hint": "s/x.pdf",
                    "page": page + 1,
                    "kind": kind,
                    "lang": lang,
                    "deck": deck,
                }
            )
    return rows


def test_page_lang():
    assert page_lang("Algoritmul este folosit pentru sortare și căutare") == "ro"
    assert page_lang("Algoritmul este folosit pentru sortare") == "ro"
    assert page_lang("The algorithm is used for sorting and search") == "en"
    assert page_lang("x = 3; y = 4;") == "?"


def test_sample_is_deterministic_and_refs_are_ocr_pages():
    rows = synthetic_census()
    a, b = sample(rows, seed=0), sample(rows, seed=0)
    assert a == b and len(a) == 200
    refs = [p for p in a if p["ref"]]
    assert len(refs) == 40 and {p["kind"] for p in refs} <= {"mixed", "image"}
    assert sum(p["lang"] == "ro" for p in refs) == 20


def test_census_row_fields_and_manifest_round_trip(tmp_path):
    pdf = make_pdf(tmp_path / "a.pdf")
    rows = census([("s", pdf)])
    assert len(rows) == 1
    row = rows[0]
    assert row["subject"] == "s" and row["page"] == 1 and row["kind"] == "text"
    assert row["lang"] == "en" and row["deck"] is False and "a.pdf" in row["path_hint"]
    write_manifest(rows, tmp_path / "m.json")
    m = load_manifest(tmp_path / "m.json")
    assert m["version"] == 1 and m["pages"] == rows and "text" not in row


def test_load_manifest_fails_loudly_on_changed_file(tmp_path):
    pdf = make_pdf(tmp_path / "a.pdf")
    write_manifest(census([("s", pdf)]), tmp_path / "m.json")
    pdf.write_bytes(pdf.read_bytes() + b"%")
    with pytest.raises(ValueError, match="a.pdf"):
        load_manifest(tmp_path / "m.json")


def test_census_unknown_language_takes_document_majority(tmp_path):
    pdf = pdfium.PdfDocument.new()
    for text in (
        "The algorithm is used for sorting and the search is that fast",
        "x = 3; y = 4; z = 5; w = 6; v = 7; u = 8; t = 9; s = 10;",
    ):
        page = pdf.new_page(595, 842)
        font = raw.FPDFText_LoadStandardFont(pdf, b"Helvetica")
        obj = raw.FPDFPageObj_CreateTextObj(pdf, font, 12)
        units = (ctypes.c_ushort * (len(text) + 1))(*text.encode("utf-16-le")[::2], 0)
        raw.FPDFText_SetText(obj, units)
        raw.FPDFPage_InsertObject(page, obj)
        raw.FPDFPage_GenerateContent(page)
    path = tmp_path / "b.pdf"
    pdf.save(path)
    assert [r["lang"] for r in census([("s", path)])] == ["en", "en"]


def test_load_manifest_fails_loudly_on_missing_file(tmp_path):
    pdf = make_pdf(tmp_path / "a.pdf")
    write_manifest(census([("s", pdf)]), tmp_path / "m.json")
    pdf.unlink()
    with pytest.raises(FileNotFoundError, match="a.pdf"):
        load_manifest(tmp_path / "m.json")


def _row(sha, page, kind, lang, **kw):
    return {
        "subject": "s",
        "sha256": sha,
        "path_hint": f"~/{sha}.pdf",
        "page": page,
        "kind": kind,
        "lang": lang,
        "deck": True,
        **kw,
    }


def _cells_input():
    pdf_rows = [_row("a", n, "image", "ro") for n in range(1, 13)]
    pdf_rows += [_row("b", n, "image", "en") for n in range(1, 4)]
    pdf_rows += [_row("c", n, "mixed", "ro") for n in range(1, 6)]
    image_rows = [_row(f"i{n}", 1, "standalone", "ro", source="image") for n in range(10)]
    image_rows += [_row(f"j{n}", 1, "standalone", "en", source="image") for n in range(11)]
    v1 = {"version": 1, "pages": [{**pdf_rows[0], "ref": True}, {**pdf_rows[-1], "ref": True}]}
    return v1, pdf_rows, image_rows


def test_v2_keeps_every_v1_page(tmp_path):
    v1, pdf_rows, image_rows = _cells_input()
    pages, _ = sample_v2(v1, pdf_rows, image_rows, tmp_path)
    got = {(p["sha256"], p["page"]): p for p in pages}
    for p in v1["pages"]:
        assert got[(p["sha256"], p["page"])]["source"] == "pdf"


def test_reference_cells_take_ten_each_where_they_exist(tmp_path):
    v1, pdf_rows, image_rows = _cells_input()
    pages, short = sample_v2(v1, pdf_rows, image_rows, tmp_path)
    refs = [p for p in pages if p["ref"]]
    cells = {
        (k, lang): sum(p["kind"] == k and p["lang"] == lang for p in refs)
        for k in ("image", "standalone")
        for lang in ("ro", "en")
    }
    assert cells == {
        ("image", "ro"): 10,
        ("image", "en"): 3,
        ("standalone", "ro"): 10,
        ("standalone", "en"): 10,
    }
    assert short == ["image/en"]
    assert all(p["source"] == "image" for p in pages if p["kind"] == "standalone")
    assert all(p["ref"] for p in pages if p["kind"] == "standalone")
    assert sample_v2(v1, pdf_rows, image_rows, tmp_path) == (pages, short)


def test_mixed_pages_are_not_references_in_v2(tmp_path):
    v1, pdf_rows, image_rows = _cells_input()
    v1["pages"].append({**pdf_rows[-1], "ref": True})
    pages, _ = sample_v2(v1, pdf_rows, image_rows, tmp_path)
    assert not any(p["ref"] for p in pages if p["kind"] == "mixed")


def test_rendered_rows_point_at_their_source(tmp_path):
    lines = [("The graph is a pair of sets and this is the edge set of the graph.", 14)]
    pdf = pdf_fixtures.make_pdf(tmp_path / "t.pdf", [pdf_fixtures.Page(lines=lines)] * 3)
    rows = census([("s", pdf)])
    assert {(r["kind"], r["lang"]) for r in rows} == {("text", "en")}
    pages, _ = sample_v2({"version": 1, "pages": []}, rows, [], tmp_path / "out")
    rendered = [p for p in pages if p["source"] == "rendered"]
    assert len(rendered) == 3
    sources = {(r["sha256"], r["path_hint"], r["page"]) for r in rows}
    for p in rendered:
        src = p["render_of"]
        assert (src["sha256"], src["path_hint"], src["page"]) in sources
        assert p["render_scale"] == 2 and p["kind"] == "rendered" and p["page"] == 1
        png = Path(p["path_hint"]).expanduser()
        assert png.parent == tmp_path / "out" and png.is_file() and not p["ref"]


def test_rendered_images_are_gitignored():
    root = Path(__file__).parents[2]
    probe = ["git", "-C", str(root), "check-ignore", "-q", "bench/reference/rendered/x.png"]
    assert subprocess.run(probe, check=False).returncode == 0


def test_image_census_tags_language_from_ocr_text(tmp_path):
    png = tmp_path / "scan.png"
    Image.new("RGB", (16, 16)).save(png)

    def read(path, pages, lang):
        doc = DoclingDocument(name="x")
        doc.add_text(DocItemLabel.TEXT, "Aceasta este o lecție și un exemplu pentru studenți")
        yield 1, doc

    (row,) = image_census([("s", png)], read)
    assert (row["kind"], row["source"], row["lang"], row["page"]) == (
        "standalone",
        "image",
        "ro",
        1,
    )


def test_v2_manifest_records_short_cells(tmp_path):
    write_manifest([], tmp_path / "m.json", version=2, short_cells=["image/en"])
    assert json.loads((tmp_path / "m.json").read_text())["short_cells"] == ["image/en"]
