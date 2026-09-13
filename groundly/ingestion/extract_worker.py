"""Extraction worker: `python -m groundly.ingestion.extract_worker <in> <out.json> [ocr_lang]`.

Always a child process, so a parser crash on a hostile or broken file kills only this
process, not the indexing run (UC-01 A2). OCR uses docling's bundled RapidOCR. Exits
EXIT_NO_TEXT when nothing is readable even after OCR, and EXIT_MODEL_UNAVAILABLE when a
model cannot load: an environment failure, retryable, never a bad document.

Output JSON: {"pages": N|null, "chunks": [{"text", "heading_path", "page", "token_count"}]}
"""

import json
import os
import sys
import tempfile
from pathlib import Path

from groundly.ingestion.formats import DOCLING_FORMATS, DOCLING_SUFFIXES, IMAGE_SUFFIXES

# Silence the fast-tokenizer advisory HybridChunker triggers. The env var, not
# logging.setLevel: transformers resets its logger level on first (lazy) import.
os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")

EXIT_NO_TEXT = 3
EXIT_MODEL_UNAVAILABLE = 4
EXIT_INPUT_TOO_LARGE = 5
# ~100 MP (10000×10000), far above any course image: bounds decompression-bomb memory
# before docling rasterizes an image.
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
    """Frame 0 of a standalone image, after the pixel cap. A multi-frame raster would become
    N docling pages merged into chunks cited to page 1, so only the first frame is indexed;
    a multi-page scan belongs in a PDF."""
    from PIL import Image, ImageSequence

    img = Image.open(path)  # lazy: reads header (size) without decoding pixels
    w, h = img.size
    cap = int(os.environ.get("GROUNDLY_MAX_IMAGE_PIXELS") or MAX_IMAGE_PIXELS)
    if w * h > cap:
        print(
            f"image too large: {w}x{h} ({w * h // 1_000_000} MP) exceeds {cap // 1_000_000} MP cap",
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
    # OCR on explicitly, with the engine pinned to RapidOCR/onnxruntime: docling's "auto"
    # would switch engines if another appeared in the environment. A non-default ocr_lang
    # (decision 15) is also passed as Rec.lang_type, because docling maps ISO codes to a
    # "latin" model group that rapidocr's default multilingual rec model rejects. Any model
    # fetch is sha256-pinned and happens in initialize_pipeline (EXIT_MODEL_UNAVAILABLE).
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
    # IMAGE needs the same options, or docling's auto OCR picks a different engine (ocrmac,
    # easyocr) than the pinned RapidOCR (decision 14).
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
        # HybridChunker drops a doc whose only content is headings (a title-only slide), though
        # OCR read its text: keep that as one chunk rather than exit with a false "found nothing".
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
