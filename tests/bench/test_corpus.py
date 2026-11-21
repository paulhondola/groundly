import ctypes
import itertools
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from pypdfium2 import raw

from bench.common.corpus import census, load_manifest, page_lang, sample, write_manifest


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
