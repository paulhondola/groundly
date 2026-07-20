"""Extraction worker — runs as `python -m groundly.ingestion.extract_worker <in> <out.json> [ocr_lang]`.

Always a child process: a parser crash on a hostile/broken file kills this process,
not the indexing run (UC-01 A2, security.md §3). OCR runs via docling's bundled
RapidOCR (local, offline, zero-key) for scanned/bitmap PDF content; a document with
no readable text even after OCR exits with EXIT_NO_TEXT so the parent reports the
specific cause. A model that can't be loaded (uncached + offline, HF rate-limit,
missing dep) exits with EXIT_MODEL_UNAVAILABLE — an environment failure, retryable,
never a bad document.

Output JSON: {"pages": N|null, "chunks": [{"text", "heading_path", "page", "token_count"}]}
"""

import json
import os
import sys
import tempfile
from pathlib import Path

from groundly.ingestion.formats import DOCLING_FORMATS, DOCLING_SUFFIXES, IMAGE_SUFFIXES

# silence the XLMRobertaTokenizerFast "__call__ is faster" advisory that
# HybridChunker's pad() calls trigger in this worker process. Must be the env var,
# not logging.setLevel: transformers resets its root logger level on first import,
# clobbering any level set before the (lazy) import; the env var is read per call.
os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")

EXIT_NO_TEXT = 3
EXIT_MODEL_UNAVAILABLE = 4
EXIT_INPUT_TOO_LARGE = 5
# ~100 MP (a 10000×10000 raster): course screenshots/photos/scans sit far below. Bounds
# decompression-bomb memory before docling rasterizes an image (security.md §threat-model).
MAX_IMAGE_PIXELS = 100_000_000


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


def _first_frame(path: Path) -> Path:
    """Standalone images are single-page by contract (page-1 attribution). A multi-frame
    raster (multi-page TIFF, animated WEBP) would otherwise expand to N docling pages that
    HybridChunker merges into one chunk carrying only the *first* page number — a citation
    resolving to the wrong page. Index frame 0 only; a multi-page scan belongs in a PDF.
    Single-frame images (the overwhelming case) pass straight through."""
    from PIL import Image, ImageSequence

    img = Image.open(path)  # lazy: reads header (size) without decoding pixels
    w, h = img.size
    if w * h > MAX_IMAGE_PIXELS:
        print(
            f"image too large: {w}x{h} ({w * h // 1_000_000} MP) exceeds "
            f"{MAX_IMAGE_PIXELS // 1_000_000} MP cap",
            file=sys.stderr,
        )
        sys.exit(EXIT_INPUT_TOO_LARGE)
    if getattr(img, "n_frames", 1) <= 1:
        return path
    fd, tmp = tempfile.mkstemp(
        suffix=path.suffix
    )  # ponytail: leaks on the rare multi-frame image; OS-cleaned
    os.close(fd)
    ImageSequence.Iterator(img)[0].convert("RGB").save(tmp)
    return Path(tmp)


def _extract_docling(path: Path, ocr_lang: str | None = None) -> dict:
    from docling.chunking import HybridChunker
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
    from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption
    from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer

    from groundly.core.manifest import CHUNK_MAX_TOKENS

    input_format = InputFormat(DOCLING_FORMATS[path.suffix.lower()])
    if path.suffix.lower() in IMAGE_SUFFIXES:
        path = _first_frame(path)
    # explicit, not inherited: do_ocr=True runs OCR on scanned/bitmap PDF content
    # The engine is pinned to RapidOCR/onnxruntime — docling's
    # "auto" would silently switch engines (ocrmac, easyocr with runtime model
    # downloads) if one ever appears in the environment. A non-default ocr_lang
    # (decision 15) is passed straight through as Rec.lang_type via rapidocr_params
    # (applied last): docling's own lang mapping collapses ISO codes to the
    # PP-OCRv4-era "latin" model group, which rapidocr 3.9's default PP-OCRv6
    # multilingual rec model rejects — while accepting the ISO code itself. Any model
    # fetch this triggers is sha256-pinned (modelscope.cn) and happens inside
    # initialize_pipeline; a fetch failure exits EXIT_MODEL_UNAVAILABLE, never a
    # document failure.
    ocr_options = (
        RapidOcrOptions(
            backend="onnxruntime",
            lang=[ocr_lang],
            rapidocr_params={"Rec.lang_type": ocr_lang},
        )
        if ocr_lang
        else RapidOcrOptions(backend="onnxruntime")
    )
    pipeline_options = PdfPipelineOptions(do_ocr=True, ocr_options=ocr_options)
    # IMAGE gets the same options so standalone images OCR with the pinned RapidOCR
    # engine — without this, docling's auto OCR selection picks a *different* engine
    # (ocrmac on macOS, easyocr elsewhere with runtime downloads) than the decision-14
    # interchange pin, exactly the silent switch the pinning above exists to prevent.
    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options),
        }
    )
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

    if not chunks:
        # HybridChunker drops docs whose only content is headings (a title-only slide or
        # page): OCR *did* read the text, so keep it as one chunk rather than exiting
        # EXIT_NO_TEXT with a "found nothing" message that isn't true. Only fires when the
        # chunker produced nothing — a doc with body text never reaches here.
        salvaged = [t for t in doc.texts if t.text and t.text.strip()]
        if salvaged:
            text = "\n".join(t.text.strip() for t in salvaged)
            page = next((t.prov[0].page_no for t in salvaged if t.prov), None)
            chunks.append(
                {
                    "text": text,
                    "heading_path": None,
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
    ocr_lang = sys.argv[3] if len(sys.argv) > 3 else None

    if in_path.suffix.lower() in DOCLING_SUFFIXES:
        result = _extract_docling(in_path, ocr_lang)
    else:
        result = _extract_plain_text(in_path)

    if not any(c["text"].strip() for c in result["chunks"]):
        # no readable text even after OCR — nothing to index
        sys.exit(EXIT_NO_TEXT)

    out_path.write_text(json.dumps(result))


if __name__ == "__main__":
    main()
