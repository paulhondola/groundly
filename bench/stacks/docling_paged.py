"""Every PDF through docling whole, then chunked one page at a time, so every chunk
cites its own page: the router's per-page citations with no routing."""

import sys
from pathlib import Path

from bench.stacks import docling_standard
from groundly.ingestion import extract_worker

SUFFIXES = frozenset({".pdf"})
page_texts = docling_standard.page_texts


def extract(path: Path, ocr_lang: str | None) -> dict:
    from docling.datamodel.base_models import InputFormat

    extract_worker._model_step(
        lambda: extract_worker._converter(ocr_lang).initialize_pipeline(InputFormat.PDF)
    )
    doc = extract_worker._converter(ocr_lang).convert(path).document
    chunks = []
    for n in sorted(doc.pages):
        chunks += extract_worker.chunk_document(doc.filter(page_nrs={n}), page=n)
    return {"pages": len(doc.pages) or None, "chunks": chunks}


if __name__ == "__main__":
    from bench.stacks._worker import main

    main(sys.modules[__name__])
