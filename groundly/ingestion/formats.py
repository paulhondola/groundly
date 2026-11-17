"""Format tables and plain-text probe shared by the pipeline and the extraction worker.
Stdlib-only: extract_worker imports this at module load, before it decides whether it even
needs docling — pulling in a heavier module here would defeat that."""

import codecs
from pathlib import Path

DOCLING_FORMATS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".md": "md",
    ".html": "html",
    ".htm": "html",
    ".xhtml": "html",
    ".tex": "latex",
    ".latex": "latex",
    ".adoc": "asciidoc",
    ".asciidoc": "asciidoc",
    ".csv": "csv",
    ".xlsx": "xlsx",
    ".epub": "epub",
}
# Standalone raster images: docling routes InputFormat.IMAGE ("image") through the
# same StandardPdfPipeline as PDFs, so they OCR on the identical pinned-RapidOCR path
# (a photographed slide / screenshot of notes indexes like a one-page scanned PDF).
IMAGE_FORMATS = {
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".tif": "image",
    ".tiff": "image",
    ".bmp": "image",
    ".webp": "image",
}
DOCLING_FORMATS.update(IMAGE_FORMATS)
IMAGE_SUFFIXES = set(IMAGE_FORMATS)  # exported for extract.py's OCR failure-message branch
DOCLING_SUFFIXES = set(DOCLING_FORMATS)
# Rendered to Markdown from their cells, then chunked by docling like any .md.
NOTEBOOK_SUFFIXES = {".ipynb"}
# Machine-generated or vector text formats that decode as UTF-8 but are noise in a course index.
SKIPPED_TEXT_SUFFIXES = {".lock", ".svg", ".map"}
PLAIN_TEXT_PROBE_BYTES = 8192


def is_supported_file(path: Path) -> bool:
    """True when `path` is a known Docling/notebook format or probes as UTF-8 plain text."""
    suffix = path.suffix.lower()
    if suffix in DOCLING_SUFFIXES or suffix in NOTEBOOK_SUFFIXES:
        return True
    if suffix in SKIPPED_TEXT_SUFFIXES:
        return False
    try:
        with open(path, "rb") as f:
            chunk = f.read(PLAIN_TEXT_PROBE_BYTES)
    except OSError:
        return False
    if b"\x00" in chunk:
        return False
    try:
        codecs.getincrementaldecoder("utf-8")().decode(
            chunk, final=len(chunk) < PLAIN_TEXT_PROBE_BYTES
        )
    except UnicodeDecodeError:
        return False
    return True
