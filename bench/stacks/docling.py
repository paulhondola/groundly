"""Groundly's own reader: docling + RapidOCR, page by page, as the product indexes."""

import sys

from groundly.ingestion import extract_worker
from groundly.ingestion.formats import IMAGE_SUFFIXES

SUFFIXES = frozenset({".pdf"}) | IMAGE_SUFFIXES
read = extract_worker.docling_pages

if __name__ == "__main__":
    from bench.stacks._worker import main

    main(sys.modules[__name__])
