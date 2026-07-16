"""Worker-internal tests: import extract_worker directly rather than going through
the subprocess (extract.py) boundary."""

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
