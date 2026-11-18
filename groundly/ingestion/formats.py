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
# Key and credential files decode as text but must never be indexed: materials/ exports whole.
SECRET_SUFFIXES = {".key", ".pem", ".env"}
SECRET_NAMES = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
PLAIN_TEXT_PROBE_BYTES = 8192
# ponytail: fixed cap on plain text only (data dumps, logs); a config key if a course needs more
PLAIN_TEXT_MAX_BYTES = 10 * 1024 * 1024
# C0 control bytes that never appear in text; their absence lets a legacy 8-bit file
# (cp1250, ISO-8859-2) through when it is not UTF-8.
_BINARY_BYTES = bytes(set(range(32)) - set(b"\t\n\r\f\v\x1b"))


def skip_reason(path: Path) -> str | None:
    """None when `path` is a known Docling/notebook format or probes as plain text, else
    why it is skipped, naming the cause."""
    if not path.is_file():  # a FIFO would block open() forever
        return "not a regular file"
    suffix = path.suffix.lower()
    if suffix in SECRET_SUFFIXES or path.name in SECRET_NAMES:
        return "looks like a key or credentials file — never indexed, it would ship in exports"
    if suffix in DOCLING_SUFFIXES or suffix in NOTEBOOK_SUFFIXES:
        return None
    if suffix in SKIPPED_TEXT_SUFFIXES:
        return f"generated file type {suffix!r} — not course text"
    if path.stat().st_size > PLAIN_TEXT_MAX_BYTES:
        return (
            f"text file over {PLAIN_TEXT_MAX_BYTES // 2**20} MB — too large to index as plain text"
        )
    try:
        with open(path, "rb") as f:
            chunk = f.read(PLAIN_TEXT_PROBE_BYTES)
    except OSError as e:
        return f"cannot read: {e.strerror}"
    if b"\x00" in chunk:
        return "binary content, or text in UTF-16/UTF-32 (save it as UTF-8)"
    try:
        codecs.getincrementaldecoder("utf-8")().decode(
            chunk, final=len(chunk) < PLAIN_TEXT_PROBE_BYTES
        )
    except UnicodeDecodeError:
        if any(b in _BINARY_BYTES for b in chunk):
            return "binary content — not text"
    return None
