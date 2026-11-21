from collections import Counter

import pytest

from bench.common.corpus import census
from bench.extract.score import diacritic_recall, score, word_f1, words
from tests.bench.test_corpus import make_pdf


def test_markup_is_not_words():
    assert words("## Titlu\n<!-- image -->\n[link](http://x)") == Counter({"titlu": 1, "link": 1})


def test_word_f1():
    assert word_f1("alpha beta gamma", "alpha beta gamma") == 1.0
    assert word_f1("alpha beta gamma delta", "alpha beta") == pytest.approx(2 / 3)


def test_cedilla_shows_up_unless_folded():
    assert diacritic_recall("știință", "ştiinţă", fold=False) == 0.0
    assert diacritic_recall("știință", "ştiinţă", fold=True) == 1.0
    assert diacritic_recall("plain", "plain", fold=False) is None


def _manifest(tmp_path):
    pages = []
    for i in range(2):
        pdf = make_pdf(tmp_path / f"d{i}.pdf", f"alpha beta gamma delta {i}")
        pages += census([("s", pdf)])
    pages = [{**p, "ref": False} for p in pages]
    return {"pages": pages}


def _out(page, text, error=None):
    return {"sha256": page["sha256"], "page": page["page"], "text": text, "ms": 1, "error": error}


def test_missing_page_counts_as_failed(tmp_path):
    m = _manifest(tmp_path)
    out = score(m, [_out(m["pages"][0], "alpha beta gamma delta 0")], reference_dir=tmp_path)
    assert out["failed_pages"] == 1 and out["scored_pages"] == 2


def test_error_page_scores_zero_and_ok_page_scores_one(tmp_path):
    m = _manifest(tmp_path)
    p0, p1 = m["pages"]
    out = score(m, [_out(p0, "alpha beta gamma delta 0"), _out(p1, "", error="boom")], tmp_path)
    key = f"word_f1/{p0['kind']}/{p0['lang']}"
    assert out["failed_pages"] == 1 and out[key] == pytest.approx(0.5)


def _ref_manifest(tmp_path):
    m = _manifest(tmp_path)
    m["pages"] = [{**p, "ref": True} for p in m["pages"]]
    return m


def test_unchecked_ref_page_is_skipped(tmp_path):
    m = _ref_manifest(tmp_path)
    p0, p1 = m["pages"]
    (tmp_path / f"{p0['sha256']}-{p0['page']}.txt").write_text("alpha beta", encoding="utf-8")
    out = score(m, [_out(p0, "alpha beta"), _out(p1, "alpha")], tmp_path)
    assert out["skipped_pages"] == 1 and out["scored_pages"] == 1 and out["failed_pages"] == 0
    assert out[f"pages/{p0['kind']}/{p0['lang']}"] == 1


def test_errored_unchecked_ref_page_still_fails(tmp_path):
    m = _ref_manifest(tmp_path)
    p0, p1 = m["pages"]
    out = score(m, [_out(p0, "", error="boom"), _out(p1, "x")], tmp_path)
    assert out["failed_pages"] == 1 and out["skipped_pages"] == 2 and out["scored_pages"] == 0


def test_empty_reference_and_output_scores_one(tmp_path):
    m = _ref_manifest(tmp_path)
    p0 = m["pages"][0]
    (tmp_path / f"{p0['sha256']}-{p0['page']}.txt").write_text("", encoding="utf-8")
    out = score({"pages": [p0]}, [_out(p0, "")], tmp_path)
    assert out[f"word_f1/{p0['kind']}/{p0['lang']}"] == 1.0


def test_errored_page_on_blank_reference_scores_zero(tmp_path):
    m = _ref_manifest(tmp_path)
    p0 = m["pages"][0]
    (tmp_path / f"{p0['sha256']}-{p0['page']}.txt").write_text("", encoding="utf-8")
    out = score({"pages": [p0]}, [_out(p0, "", error="boom")], tmp_path)
    assert out[f"word_f1/{p0['kind']}/{p0['lang']}"] == 0.0
    assert score({"pages": [p0]}, [], tmp_path)[f"word_f1/{p0['kind']}/{p0['lang']}"] == 0.0
