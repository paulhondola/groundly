# PDF page router — design

Status: draft for Paul's review (2026-11-20). Becomes the next free decision number in
`docs/groundly-spec.md` §7 when approved (recorded with `/decision` in the same change set as the code).

## Intent

Indexing is 74 % extraction, and most of that is docling running its layout model and TableFormer
over PDF pages that already carry a text layer. On apd (187 files, 782 PDF pages) a fresh
`groundly index` takes 240 s, of which PDFs take 143 s. A per-page router that reads text-layer
pages straight from the PDF and keeps docling for pages that need it measured 139 s end to end
(PDFs 43 s), with retrieval quality statistically indistinguishable from today's index and more
precise page citations. This design turns that spike into Groundly's PDF path.

Evidence (spikes, kept outside the repo in `groundly-rs/bench/extract-alt/`, `RESULTS.md` and
`route/RESULTS.md`):

- Page census, all 81 PDFs / 4,224 pages: 64 % text-layer pages, 4 % mixed (text + large image),
  27 % image-only (almost all one scanned course, avmd), 5 % blank. Only 18 PDFs have no
  image/mixed page, so routing must be per page, not per file.
- Text-layer parsers on 101 apd pages: pypdfium2 1.2 ms/page and lossless against the text layer;
  docling 202 ms/page. pymupdf4llm rejected (AGPL-3.0, drops ~10 % of words — chart labels and
  numbers). pdfplumber works but is 12× slower than pypdfium2 for the same text.
- Retrieval (Groundly `search`, 50 labelled questions written from randomly sampled apd pages):
  hit@8 0.92 vs 0.80 on the exact page; within ±1 page both 0.98, MRR 0.733 vs 0.803. Per query
  11 better / 8 worse / 31 same (sign test p ≈ 0.65). Wins come from docling chunks spanning several
  slides and citing only the first; losses from code slides split from their neighbour.
- MLX vision-language models (MinerU, PaddleOCR-VL, granite-docling) were measured and are not part
  of this design: 13–42× slower than docling on digital pages, and two of them hallucinate or loop.

## Goals

- Text-layer pages of slide-deck PDFs are extracted without docling's models.
- Pages that need layout or OCR (mixed, image-only) still go through docling exactly as today.
- Every chunk cites the page its text comes from.
- A PDF with only text-layer pages never loads docling's models (layout, TableFormer, OCR). torch
  is still imported in the worker: the bge-m3 tokenizer load and HybridChunker's import pull it in
  whenever it is installed (checked), as today's plain-text path already does. The worker is not a
  long-running process, so the torch invariant is unaffected.
- Output contract of the extraction worker is unchanged:
  `{"pages": N, "chunks": [{"text", "heading_path", "page", "token_count"}]}`.

## Non-goals

- No change to non-PDF formats, notebooks, plain text, images, embedding, retrieval, or the parent
  side of extraction (`extract.py`, `pipeline.py`, the line protocol, timeouts, failure messages).
- No VLM, no new OCR engine, no table detection on text pages (v1).
- No migration of existing subjects: their chunks stay until `groundly remove` + `groundly index`
  (decision 43's rule).
- No configuration switch to turn the router off.

## Anti-goals (a failure even if it works)

- Chunk text that is not verbatim page text (beyond the heading prepended by `contextualize`, as today).
- A text-layer page that loses text docling would have kept. Images covering < 5 % of a page stay un-OCR'd, as docling 2.115 leaves them today.
- torch imported in the parent or any long-running process (architecture invariant).
- A new failure mode the parent cannot name.

## Constraints

- Architecture invariants in `.claude/rules/`: one warm worker per run, per-file isolation, the
  four named failure messages, torch never in long-running processes on Apple Silicon, docling
  pinned at 2.115 (word-order OCR bug above it), grounding (every chunk citable, verbatim).
- Exact pins are the interchange contract; pypdfium2 becomes a direct dependency.
- `material_headings()` (the outline the map skill reads) groups distinct
  `(file, page, heading_path)`; heading noise reaches the topic-tree workflow.

## Approaches considered

- **A. Per-page chunks (chosen by Paul).** Route each page; chunk each text-layer page on its own;
  an untitled page inherits the last title seen earlier in the same PDF. What the spike measured;
  least code; precise citations. Costs: heuristic headings (~20 % false on apd), no TableFormer
  structure on text pages (text kept), small top-1 cost on split code slides.
- **B. One docling document per PDF.** Build docling-core items for text pages, `concatenate` with
  docling-converted ranges, chunk the whole document as today. Keeps today's chunk shapes and
  cross-slide merging, but gives back the citation gain (multi-page chunks cite their first page),
  unmeasured, more code. Rejected.
- **C. Per-file routing.** Only PDFs with no mixed/image page take the fast path: 18 of 81 files,
  750 of 4,224 pages. Loses most of the gain. Rejected.

## Design

### Where it lives

A new module `groundly/ingestion/pdf_router.py`, imported only by `extract_worker.py` inside the
worker process. `_handle` sends `.pdf` to `pdf_router.extract(path, ocr_lang)` instead of
`_extract_docling`; every other suffix is unchanged. The router returns the same dict shape.

### Document kind: slides or document

A PDF is a **slide deck** when most of its pages are landscape (width ≥ height). Anything else
(books, papers, portrait handouts: 7 of 81 PDFs today, e.g. sma's 138-page Android book) goes to
`_extract_docling` whole, exactly as today. The slide-title heading rule is only valid for slides.

### Page classes (slide decks)

Per page, from pypdfium2: `chars` = stripped text-layer length; `cover` = summed on-page area of
image objects / page area, capped at 1.0.

| Class | Rule | Route |
|---|---|---|
| text | chars ≥ 40, cover < `IMG_COVER` | text route |
| blank | chars < 40, cover < `IMG_COVER` | text route (title-only slides) |
| mixed | chars ≥ 40, cover ≥ `IMG_COVER` | docling route |
| image | chars < 40, cover ≥ `IMG_COVER` | docling route |

Thresholds are module constants: `MIN_CHARS = 40`, `IMG_COVER = 0.05` (Paul, 2026-11-20: docling
parity). docling 2.115 OCRs every bitmap covering ≥ 5 % of a page (`bitmap_area_threshold`), so any
page with such an image keeps docling's route and nothing docling reads today is lost. The faster 0.3
was rejected: text-route pages would lose the text inside images covering 5–30 % of the page (code
screenshots, diagram labels) — 543 of the 2,909 text/blank pages across all subjects (apd 8 %,
sma 33 %). Measured on apd, M1 Pro, spike router:

| `IMG_COVER` | Pages to docling | PDFs | `groundly index apd` | Labelled hit@8 (±1) / MRR@8 (±1) |
|---|---|---|---|---|
| today (all docling) | 782 | 143 s | 240 s | 0.98 / 0.803 |
| 0.3 | 48 | 43 s | 139 s | 0.98 / 0.733 |
| 0.05 | 105 | 79 s | 178 s | 0.98 / 0.743 |

The labelled set does not target text inside images, so it cannot show what 0.05 recovers.

### Text route

1. Read the page's characters with pypdfium2 (`FPDFText_GetUnicode`, `FPDFText_GetFontSize`),
   split into visual lines on pdfium's generated line breaks. Normalize pdfium's soft-hyphen marker
   (U+FFFE, seen as "message￾passing") to the text docling would produce.
2. **Slide title:** the page's body size is its character-weighted modal font size; the title is
   the leading run of lines with median size ≥ 1.15 × body and ≥ 3 letters. Nothing after the first
   body line is a heading.
3. Build a `DoclingDocument` directly with docling-core: title lines as one section header, body
   lines as text items, each with provenance `page_no = n`. **No Markdown is generated or parsed**:
   in the spike, Markdown round-tripping turned code lines like `#include <stdio.h>` into headings
   and stripped the `#`, which would break the verbatim rule.
4. **Title carry-forward:** a page with no title gets the most recent title from an earlier page of
   the same PDF (text route or docling route) as its heading, so continuation slides keep their
   section in `heading_path` and in the outline — the same context today's whole-document chunking
   gives them.
5. Chunk that page's document with the same `HybridChunker` (bge-m3 tokenizer at the pinned
   revision, `CHUNK_MAX_TOKENS`, `merge_peers=True`) and `contextualize`, exactly as
   `_extract_docling` does. Every chunk's `page` is `n`. A page whose chunker output is empty but
   whose text is not keeps its text as one chunk (today's title-only salvage).

### Docling route

Each mixed/image page is converted on its own with Groundly's existing `_converter(ocr_lang)` and
`page_range=(n, n)`, then chunked as today; every chunk cites page `n`. Per-page conversion is what
the spike measured and keeps the goal that every chunk cites its own page (builder's call: converting
runs of consecutive pages would let chunks span pages again). The converter is loaded only
when a PDF has such a run, so a text-only deck never loads docling's models. `page_range` keeps the
original page numbers in provenance (checked on docling 2.115: range (5, 6) yields pages 5 and 6). The model
load stays wrapped in `_model_step` (retryable `EXIT_MODEL_UNAVAILABLE`).

### Assembly and failures

- Chunks are emitted in page order; `pages` is the PDF's page count.
- pypdfium2 cannot open the file (encrypted, corrupt): hand the whole file to `_extract_docling`,
  so the failure, if any, is docling's and keeps today's message.
- No readable text anywhere after both routes: `EXIT_NO_TEXT` as today.
- Per-file timeout, size limit and crash isolation are untouched (they live in the parent).

### Manifest, docs, pins

- `Chunking.strategy` default becomes `"page-routed-hybrid"`. It is a label only: nothing reads it
  (checked), so no `format_version` bump; old bundles keep `"docling-hybrid"`.
- pypdfium2 becomes a direct dependency at the exact version docling 2.115 currently resolves
  (5.14.0 in `uv.lock`). Whether it joins the guarded pin list in `.claude/hooks/guard-pins.sh` is
  Paul's call (see Open questions).
- Docs updated in the same change set: `groundly-spec.md` §4 Extraction row and §7 new decision;
  `tech-stack.md` extraction row and pins; `data-model.md` chunking strategy and heading-path
  source; `retrieval.md` chunking paragraph; `overview.md` ingestion line; UC-01 if it names the path.

## Acceptance

1. All existing tests pass, fast and slow. They do not exercise the router: their PDF fixtures are
   portrait (whole-file docling path), the scanned-PDF tests call `_extract_docling` directly, and the
   title-only salvage test uses a PNG. The router's routes need new landscape fixtures (below).
2. New fast tests (generated landscape PDFs, no model downloads where possible): page classification for each
   class; slide/document detection; slide-title rule; title carry-forward; a code line starting with
   `#` stays verbatim and is not a heading; soft-hyphen normalization; every text-route chunk cites
   its own page; a text-only deck never calls `_converter` (docling's models stay unloaded).
3. Slow test: a landscape deck with a text page, a blank title page, an image page and a mixed page
   yields chunks from both routes with the right pages, and a blank deck exits `EXIT_NO_TEXT`.
4. A recorded measurement, not a CI test (measurement protocols stay outside the repo): on the M1 Pro,
   a fresh `groundly index apd` is ≤ 190 s (spike at `IMG_COVER = 0.05`: 178 s; today 240 s),
   and the 50-question labelled set (`groundly-rs/bench/extract-alt/route/queries_labeled.json`,
   scored by `eval_retrieval.py` against a fresh docling index) gives hit@8 within ±1 page ≥ 0.96.
   The built design differs from the spike in one place: the text route builds a `DoclingDocument`
   directly instead of going through Markdown. If the retrieval gate misses, first rerun the spike's
   Markdown route on the same queries to see whether the direct document is the cause.

## Left to the builder

- Grouping body lines into text items (one per line or per paragraph) — HybridChunker merges either way.
- How the text route's `DoclingDocument` provenance bounding boxes are filled (docling-core requires one).
- Fixture construction for the new tests.
- Whether classification and text extraction share one textpage per page.

## Open questions for Paul

- Add pypdfium2 to the guarded pin list? It shapes chunk text the way rapidocr does.
