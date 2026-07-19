"""Worker-internal tests: import extract_worker directly rather than going through
the subprocess (extract.py) boundary."""

from pathlib import Path

import pytest


def test_docling_model_fetch_failure_exits_retryable(monkeypatch, tmp_path):
    """Docling's layout models load before the document is parsed: a fetch failure
    (cold HF cache + offline) must exit EXIT_MODEL_UNAVAILABLE (retryable), never be
    recorded as the PDF's terminal parse failure."""
    from docling.document_converter import DocumentConverter

    from groundly.ingestion import extract_worker

    def offline_init(self, format):
        raise OSError("couldn't connect to huggingface.co")

    def no_convert(self, path):
        raise AssertionError("document must not be parsed when models are unavailable")

    monkeypatch.setattr(DocumentConverter, "initialize_pipeline", offline_init)
    monkeypatch.setattr(DocumentConverter, "convert", no_convert)
    with pytest.raises(SystemExit) as exc:
        extract_worker._extract_docling(tmp_path / "lec.pdf")
    assert exc.value.code == extract_worker.EXIT_MODEL_UNAVAILABLE


def _captured_format_options(monkeypatch, ocr_lang):
    """Run _extract_docling with a stub DocumentConverter and return the captured
    format_options — construction only, no models, no parsing."""
    import docling.document_converter as dc

    from groundly.ingestion import extract_worker

    captured = {}

    class StubConverter:
        def __init__(self, format_options):
            captured.update(format_options)

        def initialize_pipeline(self, format):
            raise OSError("stop before any model load")

    monkeypatch.setattr(dc, "DocumentConverter", StubConverter)
    with pytest.raises(SystemExit):  # the stub's OSError → EXIT_MODEL_UNAVAILABLE
        extract_worker._extract_docling(Path("lec.pdf"), ocr_lang)
    return captured


def test_ocr_lang_lands_in_rapidocr_options(monkeypatch):
    """--ocr-lang must reach RapidOcrOptions — both lang and the Rec.lang_type
    pass-through (docling's own mapping yields 'latin', which rapidocr's default
    PP-OCRv6 rec model rejects; the ISO code itself is accepted)."""
    from docling.datamodel.base_models import InputFormat

    opts = _captured_format_options(monkeypatch, "ro")
    ocr_options = opts[InputFormat.PDF].pipeline_options.ocr_options
    assert ocr_options.lang == ["ro"]
    assert ocr_options.rapidocr_params == {"Rec.lang_type": "ro"}


def test_no_ocr_lang_keeps_default_langs(monkeypatch):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import RapidOcrOptions

    opts = _captured_format_options(monkeypatch, None)
    ocr_options = opts[InputFormat.PDF].pipeline_options.ocr_options
    assert ocr_options.lang == RapidOcrOptions(backend="onnxruntime").lang


def _image_only_pdf(path, lines):
    # ponytail: PIL's own "PDF" save format is a real (if minimal) single-image-per-page
    # PDF — no need for reportlab/img2pdf just to build a fixture. Page-sized canvas +
    # large font matter: a small font/canvas OCRs to nothing. There is no text layer,
    # so a passing test proves OCR ran (with do_ocr=False it fails at "OCR found no text").
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1700, 2200), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 80)
    for i, line in enumerate(lines):
        draw.text((100, 200 + i * 150), line, fill="black", font=font)
    img.save(path, "PDF", resolution=200.0)


@pytest.mark.slow
def test_scanned_pdf_indexes_via_ocr(tmp_path):
    """A scanned (image-only) PDF with no text layer must be readable via docling's
    bundled RapidOCR — chunks non-empty, page attribution intact."""
    from groundly.ingestion import extract_worker

    pdf = tmp_path / "scanned.pdf"
    _image_only_pdf(pdf, ["Deadlock requires mutual exclusion", "and circular wait to occur."])

    result = extract_worker._extract_docling(pdf)

    assert result["chunks"], "OCR found no text"
    assert any("mutual exclusion" in c["text"] for c in result["chunks"])
    assert result["chunks"][0]["page"] == 1


@pytest.mark.slow
def test_scanned_pdf_romanian_diacritics_via_ocr_lang(tmp_path):
    """End-to-end through the explicit ocr_lang path (bundled models, no download):
    Romanian diacritics must survive OCR, not be flattened to ASCII. Note: the
    bundled PP-OCRv6 rec model is multilingual, so the default path reads these
    diacritics too — the plumbing guard is test_ocr_lang_lands_in_rapidocr_options."""
    from groundly.ingestion import extract_worker

    pdf = tmp_path / "scanned_ro.pdf"
    _image_only_pdf(pdf, ["Excludere mutuală și așteptare circulară", "trebuie să aibă loc."])

    result = extract_worker._extract_docling(pdf, ocr_lang="ro")

    assert result["chunks"], "OCR found no text"
    text = " ".join(c["text"] for c in result["chunks"])
    assert "mutuală" in text or ("ă" in text and "ș" in text)


@pytest.mark.slow
def test_blank_pdf_exits_no_text(tmp_path, monkeypatch):
    """A PDF with no text and no meaningful bitmap content must still exit
    EXIT_NO_TEXT — OCR doesn't invent text that isn't there."""
    from PIL import Image

    from groundly.ingestion import extract_worker

    pdf = tmp_path / "blank.pdf"
    Image.new("RGB", (1700, 2200), "white").save(pdf, "PDF", resolution=200.0)

    monkeypatch.setattr("sys.argv", ["extract_worker", str(pdf), str(tmp_path / "out.json")])
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        extract_worker.main()
    assert exc.value.code == extract_worker.EXIT_NO_TEXT
