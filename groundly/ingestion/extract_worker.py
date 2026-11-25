"""Extraction worker: `python -m groundly.ingestion.extract_worker <in> <out.json> [ocr_lang]`,
or `--serve` to extract many files in one process over a line protocol (see `serve`).

Always a child process, so a parser crash on a hostile or broken file kills only this
process, not the indexing run (UC-01 A2). PDFs go through the page router (`pdf_router`),
which sends a slide deck's text pages to the text layer and only its image pages to docling;
other docling formats convert whole. OCR uses docling's bundled RapidOCR. Exits
EXIT_NO_TEXT when nothing is readable even after OCR, and EXIT_MODEL_UNAVAILABLE when a
model cannot load: an environment failure, retryable, never a bad document.

Output JSON: {"pages": N|null, "chunks": [{"text", "heading_path", "page", "token_count"}]}
"""

import functools
import json
import os
import re
import sys
import tempfile
import traceback
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from groundly.ingestion.formats import (
    DOCLING_FORMATS,
    DOCLING_SUFFIXES,
    IMAGE_SUFFIXES,
    NOTEBOOK_SUFFIXES,
)

if TYPE_CHECKING:
    from docling_core.types.doc import DoclingDocument

# Silence the fast-tokenizer advisory HybridChunker triggers. The env var, not
# logging.setLevel: transformers resets its logger level on first (lazy) import.
os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")

EXIT_NO_TEXT = 3
EXIT_MODEL_UNAVAILABLE = 4
EXIT_INPUT_TOO_LARGE = 5
# ~100 MP (10000×10000), far above any course image: bounds decompression-bomb memory
# before docling rasterizes an image.
MAX_IMAGE_PIXELS = 100_000_000
# CPU threads for docling's models — RapidOCR dominates a scanned page. One worker runs at a
# time, so it may take every performance core; a thread on an efficiency core (Apple Silicon)
# or a hyper-thread sibling makes the whole pool wait on it, which measured slower than
# docling's default of 4.
INFERENCE_THREADS = 8
# Pages per docling conversion: the worker reports progress (and the parent's timeout
# restarts) once per batch, so a batch of slow OCR pages must finish within the timeout.
PAGE_BATCH = 16


@functools.cache
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
    except Exception as exc:  # noqa: BLE001 - any load failure is the retryable exit
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


@functools.cache
def _converter(ocr_lang: str | None):
    """One DocumentConverter per OCR language, kept for the process: docling caches the
    loaded pipeline inside it, so a serving worker pays model load once."""
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
    from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption

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
    pipeline_options = PdfPipelineOptions(
        do_ocr=True,
        ocr_options=ocr_options,
        accelerator_options=AcceleratorOptions(num_threads=INFERENCE_THREADS),
    )
    # IMAGE needs the same options, or docling's auto OCR picks a different engine (ocrmac,
    # easyocr) than the pinned RapidOCR (decision 14).
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options),
        }
    )


@functools.cache
def _chunker():
    """The bge-m3 token-budget tokenizer and HybridChunker, built once per process."""
    from docling.chunking import HybridChunker
    from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer

    from groundly.core.manifest import CHUNK_MAX_TOKENS

    tokenizer = HuggingFaceTokenizer(
        tokenizer=_model_step(_bge_m3_tokenizer), max_tokens=CHUNK_MAX_TOKENS
    )
    return tokenizer, HybridChunker(tokenizer=tokenizer, merge_peers=True)


def chunk_document(doc: "DoclingDocument", page: int | None = None) -> list[dict]:
    """Worker chunks for a parsed document. Each chunk cites the page of its first item,
    or `page` for every chunk when given (a one-page conversion)."""
    tokenizer, chunker = _chunker()
    chunks = []
    for chunk in chunker.chunk(doc):
        text = chunker.contextualize(chunk)  # heading path prepended — what gets embedded
        headings = getattr(chunk.meta, "headings", None) or []
        chunk_page = None
        for item in getattr(chunk.meta, "doc_items", []) or []:
            prov = getattr(item, "prov", None)
            if prov:
                chunk_page = prov[0].page_no
                break
        chunks.append(
            {
                "text": text,
                "heading_path": " > ".join(headings) or None,
                "page": page if page is not None else chunk_page,
                "token_count": tokenizer.count_tokens(text),
            }
        )

    if not chunks:
        # HybridChunker drops a doc whose only content is headings (a title-only slide), though
        # OCR read its text: keep that as one chunk rather than exit with a false "found nothing".
        salvaged = [t for t in doc.texts if t.text and t.text.strip()]
        if salvaged:
            text = "\n".join(t.text.strip() for t in salvaged)
            chunk_page = next((t.prov[0].page_no for t in salvaged if t.prov), None)
            chunks.append(
                {
                    "text": text,
                    "heading_path": None,
                    "page": page if page is not None else chunk_page,
                    "token_count": tokenizer.count_tokens(text),
                }
            )
    return chunks


def _extract_docling(path: Path, ocr_lang: str | None = None) -> dict:
    from docling.datamodel.base_models import InputFormat

    input_format = InputFormat(DOCLING_FORMATS[path.suffix.lower()])
    if path.suffix.lower() in IMAGE_SUFFIXES:
        path = _first_frame(path)
    # docling's layout models load here, before the document is touched — a fetch
    # failure is the environment's fault, never this document's parse failure
    _model_step(lambda: _converter(ocr_lang).initialize_pipeline(input_format))
    doc = _converter(ocr_lang).convert(path).document
    chunks = chunk_document(doc)
    pages = len(doc.pages) if doc.pages else None
    return {"pages": pages, "chunks": chunks}


def _page_runs(pages: list[int]) -> list[tuple[int, int]]:
    """Sorted contiguous runs of `pages`, each at most PAGE_BATCH pages long."""
    runs: list[tuple[int, int]] = []
    for n in sorted(set(pages)):
        if runs and n == runs[-1][1] + 1 and n - runs[-1][0] < PAGE_BATCH:
            runs[-1] = (runs[-1][0], n)
        else:
            runs.append((n, n))
    return runs


def docling_pages(
    path: Path, pages: list[int] | None, ocr_lang: str | None
) -> "Iterator[tuple[int, DoclingDocument]]":
    """The product's page reader: docling + RapidOCR, PAGE_BATCH pages per conversion.

    Yields (page_no, one-page document) for every requested page (every page when `pages`
    is None) in ascending order, pages with no text included, as each batch finishes. No
    conversion ever starts past the last page: docling rejects that range."""
    from docling.datamodel.base_models import InputFormat

    input_format = InputFormat(DOCLING_FORMATS[path.suffix.lower()])
    converter = _converter(ocr_lang)
    # models load before the document is touched: a fetch failure is retryable, never this
    # document's parse failure
    _model_step(lambda: converter.initialize_pipeline(input_format))

    def convert(start: int, end: int):
        result = converter.convert(path, page_range=(start, end))
        doc = result.document
        for n in range(start, end + 1):
            if n in doc.pages:
                yield n, doc.filter(page_nrs={n})
        return result.input.page_count

    if pages is not None:
        for start, end in _page_runs(pages):
            yield from convert(start, end)
        return
    total = yield from convert(1, PAGE_BATCH)
    for start in range(PAGE_BATCH + 1, total + 1, PAGE_BATCH):
        yield from convert(start, min(start + PAGE_BATCH - 1, total))


def _extract_plain_text(path: Path) -> dict:
    """Token-window chunks cut from the source by offset. Decoding the tokens instead would
    collapse all whitespace; each window starts where the last ended, so indentation at a
    window's start survives and the chunks join back into the file."""
    from groundly.core.manifest import CHUNK_MAX_TOKENS

    # ponytail: a legacy 8-bit file reads with U+FFFD for its accents; detect its encoding if that hurts search
    text = path.read_text(encoding="utf-8", errors="replace")
    tokenizer = _model_step(_bge_m3_tokenizer)
    offsets = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)[
        "offset_mapping"
    ]
    chunks = []
    end = 0
    for start in range(0, len(offsets), CHUNK_MAX_TOKENS):
        window = offsets[start : start + CHUNK_MAX_TOKENS]
        begin, end = end, window[-1][1]
        chunks.append(
            {
                "text": text[begin:end],
                "heading_path": None,
                "page": None,
                "token_count": len(window),
            }
        )
    return {"pages": None, "chunks": chunks}


def _notebook_markdown(path: Path) -> str:
    """A notebook's cells as Markdown: code fenced, saved outputs dropped. Outputs are
    machine-generated and can be megabytes of base64 that would index as junk chunks."""
    try:
        cells = json.loads(path.read_bytes())["cells"]
        parts = []
        for cell in cells:
            source = "".join(cell["source"])
            if not source.strip():
                continue
            if cell["cell_type"] == "code":
                longest = max(map(len, re.findall("`+", source)), default=0)
                fence = "`" * max(3, longest + 1)
                source = f"{fence}\n{source}\n{fence}"
            parts.append(source)
    except (ValueError, LookupError, TypeError) as exc:
        sys.exit(f"not a readable nbformat 4 notebook: {exc!r}")
    return "\n\n".join(parts)


def _extract_notebook(path: Path) -> dict:
    markdown = _notebook_markdown(path)
    if not markdown:
        return {"pages": None, "chunks": []}
    with tempfile.TemporaryDirectory() as tmp:
        rendered = Path(tmp) / "notebook.md"
        rendered.write_text(markdown, encoding="utf-8")
        return _extract_docling(rendered)


_SERVE_STATUS = {
    EXIT_NO_TEXT: "no_text",
    EXIT_MODEL_UNAVAILABLE: "model_unavailable",
    EXIT_INPUT_TOO_LARGE: "input_too_large",
}


def _handle(in_path: Path, out_path: Path, ocr_lang: str | None) -> None:
    if in_path.suffix.lower() == ".pdf":
        from groundly.ingestion import pdf_router

        result = pdf_router.extract(in_path, ocr_lang)
    elif in_path.suffix.lower() in DOCLING_SUFFIXES:
        result = _extract_docling(in_path, ocr_lang)
    elif in_path.suffix.lower() in NOTEBOOK_SUFFIXES:
        result = _extract_notebook(in_path)
    else:
        result = _extract_plain_text(in_path)

    if not any(c["text"].strip() for c in result["chunks"]):
        # no readable text even after OCR — nothing to index
        sys.exit(EXIT_NO_TEXT)

    out_path.write_text(json.dumps(result))


def serve(
    stdin: TextIO,
    stdout: TextIO,
    handle: Callable[[Path, Path, str | None], None] = _handle,
) -> None:
    """One JSON request per line in ({"in", "out", "ocr_lang"}), one reply per line out
    ({"status": ok|no_text|model_unavailable|input_too_large|error}). The cause goes to
    stderr before the reply, so the parent can read it once the reply arrives. A failing
    file never ends the loop."""
    for line in stdin:
        req = json.loads(line)
        status = "ok"
        try:
            handle(Path(req["in"]), Path(req["out"]), req["ocr_lang"])
        except SystemExit as exc:
            if isinstance(exc.code, int):
                status = _SERVE_STATUS.get(exc.code, "error")
                if status == "error":
                    print(f"exit code {exc.code}", file=sys.stderr)
            else:
                print(exc.code, file=sys.stderr)
                status = "error"
        except Exception:  # noqa: BLE001 - one bad file must not end the worker
            traceback.print_exc()
            status = "error"
        sys.stderr.flush()
        stdout.write(json.dumps({"status": status}) + "\n")
        stdout.flush()


def serve_stdio(handle: Callable[[Path, Path, str | None], None] = _handle) -> None:
    """`serve` on the process's stdin and a private copy of fd 1. fd 1 itself is pointed at
    stderr, so anything a library prints (even from C) can never corrupt the protocol."""
    reply = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    serve(sys.stdin, reply, handle)


def main() -> None:
    if sys.argv[1] == "--serve":
        serve_stdio()
        return
    _handle(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else None)


if __name__ == "__main__":
    main()
