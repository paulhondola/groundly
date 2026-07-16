"""Extraction worker — runs as `python -m groundly.ingestion.extract_worker <in> <out.json>`.

Always a child process: a parser crash on a hostile/broken file kills this process,
not the indexing run (UC-01 A2, security.md §3). Digital documents only — no OCR;
an empty text layer exits with EXIT_NO_TEXT so the parent reports the specific cause.
A model that can't be loaded (uncached + offline, HF rate-limit, missing dep) exits
with EXIT_MODEL_UNAVAILABLE — an environment failure, retryable, never a bad document.

Output JSON: {"pages": N|null, "chunks": [{"text", "heading_path", "page", "token_count"}]}
"""

import json
import sys
from pathlib import Path

EXIT_NO_TEXT = 3
EXIT_MODEL_UNAVAILABLE = 4

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
DOCLING_SUFFIXES = set(DOCLING_FORMATS)
# Everything else on the pipeline allowlist (txt + source code) is read as plain
# text and chunked by token windows — docling's converter does not accept it.


def _bge_m3_tokenizer():
    from transformers import AutoTokenizer

    from groundly.core.manifest import EMBEDDING_MODEL, HF_REVISION

    return AutoTokenizer.from_pretrained(EMBEDDING_MODEL, revision=HF_REVISION)


def _model_step(fn):
    """Model loading is the network/dependency-bound step. A failure (uncached + offline,
    HF rate-limit, missing dep) is transient — the parent retries it — so it exits
    distinctly, never collapsing into a terminal 'bad document'."""
    try:
        return fn()
    except Exception as exc:
        print(f"model unavailable: {exc}", file=sys.stderr)
        sys.exit(EXIT_MODEL_UNAVAILABLE)


def _extract_docling(path: Path) -> dict:
    from docling.chunking import HybridChunker
    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import DocumentConverter
    from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer

    from groundly.core.manifest import CHUNK_MAX_TOKENS

    input_format = InputFormat(DOCLING_FORMATS[path.suffix.lower()])
    converter = DocumentConverter()
    # docling's layout models load here, before the document is touched — a fetch
    # failure is the environment's fault, never this document's parse failure
    _model_step(lambda: converter.initialize_pipeline(input_format))
    doc = converter.convert(path).document
    tokenizer = HuggingFaceTokenizer(
        tokenizer=_model_step(_bge_m3_tokenizer), max_tokens=CHUNK_MAX_TOKENS
    )
    chunker = HybridChunker(tokenizer=tokenizer, merge_peers=True)

    chunks = []
    for chunk in chunker.chunk(doc):
        text = chunker.contextualize(chunk)  # heading path prepended — what gets embedded
        headings = getattr(chunk.meta, "headings", None) or []
        page = None
        for item in getattr(chunk.meta, "doc_items", []) or []:
            prov = getattr(item, "prov", None)
            if prov:
                page = prov[0].page_no
                break
        chunks.append(
            {
                "text": text,
                "heading_path": " > ".join(headings) or None,
                "page": page,
                "token_count": tokenizer.count_tokens(text),
            }
        )

    pages = len(doc.pages) if doc.pages else None
    return {"pages": pages, "chunks": chunks}


def _extract_plain_text(path: Path) -> dict:
    from groundly.core.manifest import CHUNK_MAX_TOKENS

    text = path.read_text(errors="replace")
    tokenizer = _model_step(_bge_m3_tokenizer)
    ids = tokenizer.encode(text, add_special_tokens=False)
    chunks = []
    for start in range(0, len(ids), CHUNK_MAX_TOKENS):
        window = ids[start : start + CHUNK_MAX_TOKENS]
        chunks.append(
            {
                "text": tokenizer.decode(window),
                "heading_path": None,
                "page": None,
                "token_count": len(window),
            }
        )
    return {"pages": None, "chunks": chunks}


def main() -> None:
    in_path, out_path = Path(sys.argv[1]), Path(sys.argv[2])

    if in_path.suffix.lower() in DOCLING_SUFFIXES:
        result = _extract_docling(in_path)
    else:
        result = _extract_plain_text(in_path)

    if not any(c["text"].strip() for c in result["chunks"]):
        # digital-documents-only rule (pivot #3): empty text layer, no OCR fallback
        sys.exit(EXIT_NO_TEXT)

    out_path.write_text(json.dumps(result))


if __name__ == "__main__":
    main()
