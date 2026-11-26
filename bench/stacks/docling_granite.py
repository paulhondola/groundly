"""Image pages through granite, every other page through docling, as the product reads."""

import sys

from bench.stacks import docling, granite
from bench.stacks.by_kind import by_kind
from groundly.ingestion.formats import IMAGE_SUFFIXES

SUFFIXES = frozenset({".pdf"}) | IMAGE_SUFFIXES
read = by_kind({"image": granite.read}, default=docling.read)

if __name__ == "__main__":
    from bench.stacks._worker import main

    main(sys.modules[__name__])
