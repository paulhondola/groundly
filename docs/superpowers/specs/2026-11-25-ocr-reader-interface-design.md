# OCR reader interface and OCR bench (2026-11-25)

## Intent

Every OCR engine Groundly might use plugs into one seam, so the bench compares engines on equal
terms and the winner ships by swapping one function.

Today each bench stack (`bench/stacks/*.py` on `bench-suite`) implements two unrelated functions:
`extract()`, which is what gets indexed, and `page_texts()`, which is what gets scored. Nothing makes
them agree (`docling_routed.page_texts` is a hand copy of `pdf_router.extract`'s loop). Each stack also
re-does the page walk, model warm-up, timing (a module-global `last_ms`) and chunking, and reaches
into private `extract_worker` functions. Whole-file stacks report amortized ms/page while per-page
stacks report true ms/page, so `ms_p50` mixes two things.

OCR accuracy itself has never been measured: `bench/reference/` does not exist, so none of the 40
reference pages has checked text, and image pages outside that set score against an empty text layer.

The numbers feed one product decision (does a VLM on image pages replace docling + RapidOCR?) and the
thesis's engine comparison. Paul is the only user.

## Goals

- One reader interface, defined by the product's own PDF and image path. Bench stacks are alternative
  readers of the same interface.
- Indexed text and scored text come from the same per-page `DoclingDocument`.
- Three engines measured: docling + RapidOCR (baseline), MinerU2.5 on MLX, granite-docling 258M on MLX.
- OCR accuracy measured on real scans and standalone images, in Romanian and English.
- A ship rule fixed before any VLM run.

## Non-goals

- No setting, flag or registry that picks the reader in the product (zero config, decision 48's spirit).
- No VLM in the product in this work. Shipping one is a separate `/decision` after the ship rule passes.
- No scoring of tables, math or reading order. The retrieval bench catches structure's effect on search.
- docx, pptx, html, md, notebooks and plain text keep today's path.
- No CLI per-page progress display. The protocol carries page lines (below); wiring them to the CLI is
  later work.
- PaddleOCR-VL (invented 12% of words on Romanian pages in the 2026-11-20 spike), Apple Vision and
  tesseract are not built.

## Anti-goals (a failure even if it runs)

- A stack scored on text it did not index.
- A reference drafted by one candidate engine alone.
- Course text in git: rendered page images, reference text, drafts and page text stay gitignored.
- `groundly/` importing `bench`, a bench dependency in the default install, or the bench extra
  changing any package the product uses.
- A verdict from runs in different sessions, or a ship rule chosen after seeing the numbers.

## Constraints

- Architecture invariants (`.claude/rules/architecture.md`): one warm extraction worker per run,
  isolation per file, specific failure messages, the worker's stdout reserved for the reply protocol.
- Trust boundary: the image pixel cap, first-frame extraction for multi-frame images and the file-size
  cap run in Groundly's code before any reader.
- docling stays pinned at 2.115.0 (`.claude/hooks/guard-pins.sh`). Its `vlm_model_specs` already has
  `GRANITEDOCLING_MLX`; that pipeline needs `mlx-vlm`, which the `bench` extra installs (0.7.12).
- Never commit to `main`. Merges to `main` go through Paul.
- Machine: M1 Pro, 16 GB. Every result row records the machine.

## Approaches considered

| Decision | Picked | Rejected, and why |
|---|---|---|
| Where the interface lives | Product seam in `groundly/ingestion`; bench stacks supply other readers through `SubprocessExtractor(argv=…)` | Bench-only: product and bench would each keep a copy of the page loop, and a winner would be rewritten into the product |
| Seam granularity | `read(path, pages)` yields one result per page; each engine batches as it likes | File-level (today): each stack re-implements walk and chunking. Image-level `ocr(image) -> markdown`: docling loses the text layer and layout that make it win on digital pages, and routing cannot be expressed |
| Reader output | One `DoclingDocument` per page; Markdown engines go through one shared helper | Markdown everywhere: the product's docling path would round-trip through Markdown and change what it indexes |
| Timeout | No page finished within `timeout_seconds` (page progress lines) | Per file: MinerU (~9 s/page) fails every scanned PDF over ~33 pages, and `NT1` already times out under docling |
| Chunking non-deck PDFs | Per page for every PDF, last heading carried forward | Per page for decks only: two code paths, and citations that point at a chunk's first page only |

## The design

The work is two parts on two branches. Part 1 is a product PR off `main`. Part 2 continues on
`bench-suite` after it merges `main`.

### Part 1: product (own branch off `main`, own PR)

**The reader.** A reader is a function:

```python
def read(
    path: Path, pages: list[int] | None, ocr_lang: str | None
) -> Iterator[tuple[int, DoclingDocument]]: ...
```

`pages=None` means every page. It yields `(page_no, one-page document)` for each requested page, in
ascending page order, as pages finish. A page with no text yields an empty document, not an error. An
exception fails the whole file (`parser failed: …`), as today.

The product has one reader, `docling_pages`, in `extract_worker.py`: docling + RapidOCR configured as
`_converter` is today, converting in page-range batches (about 16 pages; the builder tunes it) and
yielding each page as `doc.filter(page_nrs={n})`. A standalone image is a one-page input, after
`_first_frame`.

**The page loop.** `chunk_pages(pages)` in `extract_worker.py` takes any reader's output and returns
the worker result `{"pages": N, "chunks": [...]}`. It chunks each page with
`chunk_document(doc, page=n)`. A chunk with no heading of its own gets the last section header or title
seen on an earlier page (`last_heading`, moved from `pdf_router.py`) as its `heading_path`, and that
heading is prepended to its text in the same shape `HybridChunker.contextualize` produces, so the
heading is embedded.

`_handle` sends `.pdf` and the image suffixes through `chunk_pages(docling_pages(path, None, ocr_lang))`.
Every other format keeps its path. The no-text exit is unchanged.

**Page progress and the timeout.** Before its final `{"status": …}` reply, the worker writes one
`{"page": n}` line per page that `chunk_pages` consumes. The lines go through the private reply fd, never
`sys.stdout`. In `SubprocessExtractor._exchange`, each page line restarts the wait. The status line ends
the exchange. `timeout_seconds` (default 300) now means "no page finished within N s". Formats that send
no page lines keep a whole-file wait. A worker that dies after sending any page line has read the
request, so it is a parser failure and is not resent. The message names the cause: `extraction timed
out — no page finished within 300s`. The config comment and docs change with it.

**What leaves the product.** `pdf_router.py` and `tests/ingestion/test_pdf_router.py` are deleted from
the product. The router test in `test_extract_worker.py` becomes a `chunk_pages` / `docling_pages` test.
Part 2 restores the router code under `bench/` from git history.

**Decision.** `/decision` revises decision 49: no router; every PDF and standalone image is chunked
per page with the heading carried forward; the timeout is per page. That covers the spec's §4 Extraction
row and §7 register, the architecture overview, the config docs and UC-01's timeout message.

**Gate before the PR merges.** The product branch is merged into `bench-suite` locally (never into
`main`), and the retrieval gate runs there:

- About 30 Romanian queries on pm, `{query, file, page}` like `apd.json`, written by Claude and
  spot-checked by Paul, saved as `bench/retrieval/queries/pm.json`. pm holds most of the manifest's
  non-deck pages.
- One index per arm, `docling_standard` (today's `main`) against the product worker, on apd and pm,
  same session, homes kept. The index bench gains a `product` arm that runs `SubprocessExtractor`
  with its default `argv`, so the product is measured as it ships.
- Pass: the product's `hit_within1@8` matches or beats `docling_standard` on both pm and apd.
  `hit_exact@8` and `mrr_within1@8` are reported beside it.

### Part 2: bench (`bench-suite`, after it merges `main`)

**Stack contract.** A stack module exports `SUFFIXES` and `read` (a reader as above). Nothing else.
`extract`, `page_texts` and `last_ms` go away. `_worker.main(stack)` builds both modes from `read`:

- `--serve`: files matching `SUFFIXES` go through the product's `chunk_pages(stack.read(…))` (page lines
  included). Everything else goes through `extract_worker._handle`.
- `--pages`: groups the manifest's pages by (file, page kind). One untimed warm-up call is timed
  separately as `load_s`. Then it makes one timed `read(path, pages, ocr_lang)` call per group, and
  `ms = call time / pages in the call`. The text is each page document's `export_to_markdown()`.

**Readers and stacks.**

| Module | What it is |
|---|---|
| `docling` | the product reader, `extract_worker.docling_pages`; the baseline. Every stack's `SUFFIXES` is `.pdf` plus the image suffixes |
| `mineru` | page rendered at scale 2 → MinerU2.5 two-step → Markdown → `markdown_page(md, n)` |
| `granite` | docling's `VlmPipeline` with `GRANITEDOCLING_MLX`, page-range batches |
| `text_layer` | the old router's text-layer reader and `classify_page`, restored from `pdf_router.py` |
| `docling_mineru` | `by_kind({"image": mineru.read}, default=docling.read)` |
| `docling_granite` | `by_kind({"image": granite.read}, default=docling.read)` |

`markdown_page(md, n)` builds a one-page `DoclingDocument` from Markdown with docling's Markdown
converter, as `mineru.py` does today. It lives in `bench/stacks/`, since only bench engines need it.

`by_kind(readers, default)` classifies each requested PDF page with `classify_page` (a standalone image
is always `image`), calls each reader with its own pages, and yields in ascending page order. To start
with, only `image` pages go to the VLM. Whether `mixed` pages follow depends on the accuracy data. No
reader may hold back a finished page longer than the timeout allows.

`docling_standard` stays until the Part 1 gate has run. Then it is removed with `docling_paged` (now the
product) and `docling_routed` (its results stand in `index.jsonl`). The accuracy bench scores the raw
readers `docling`, `mineru` and `granite`. The index and retrieval benches run `docling`,
`docling_mineru` and `docling_granite`.

Each VLM reader caps generated tokens per page and counts the pages that hit the cap.

**Corpus manifest v2.** Version 1's pages are kept and these are added:

- **Standalone images** from the subjects' materials, stratified by language. Images have no text
  layer, so the census tags their language from docling's OCR output.
- **Rendered pages:** about 50 `text` pages per language. Each is rendered once at scale 2 (144 DPI) to
  the gitignored `bench/reference/rendered/<sha256>-<page>.png`, and every engine reads that same file as
  a standalone image. The reference is that page's text layer. The manifest records the render scale.
- **Hand-checked pages:** about 40, about 10 per {scanned PDF page, standalone image} × {ro, en}. If a
  cell has fewer candidates in the subjects, it takes what exists and the manifest says so.

Rows say which source a page came from (PDF page, standalone image, rendered). `mixed` pages still score
against the text layer, which misses text inside figures, so their scores are a lower bound and the
README says so.

**Hand check.** For each hand-checked page, Claude reads the page image next to the MinerU and docling
drafts and writes the merged text to `bench/reference/<sha256>-<page>.txt`. Paul corrects it in his
editor beside the image. Drafts are never committed.

**Metrics.** Per `kind/lang`: `word_f1`, `bigram_f1`, `word_precision`, `word_recall`,
`diacritic_recall`, `diacritic_recall_folded`, and the n behind each. Per run: `load_s`, `ms_p50`,
`peak_rss_mb`, `model_disk_mb` (the engine's weights on disk), and the count of pages that timed out or
hit the token cap.

**Ship rule.** This is written into `bench/README.md` before any VLM run. A stack ("docling, with the
VLM on image pages") replaces the product's reader only if all of these hold in one session:

- (a) On the hand-checked pages (scanned PDF pages and standalone images), `word_f1` is at least
  0.05 higher than docling's in both languages, and `word_precision` does not drop.
- (b) `hit_within1@8` does not drop on apd or pm.
- (c) apd indexes in at most 1.5× docling's median time, and no file that docling indexes in apd, pm or
  the three files that timed out (`LFTC Cursuri.pdf`, `PI_Slide-uri_3.pdf`, `NT1 - …`) fails.
- (d) The engine has a torch/transformers path off Apple Silicon.

Passing still needs a `/decision`.

**Runs, in order:**

1. Accuracy: `docling`, `mineru`, `granite` over manifest v2, one session.
2. Index: `docling`, `docling_mineru`, `docling_granite`, interleaved, 5 runs each on apd, then one each
   on pm and on the three timed-out files.
3. Retrieval on apd and pm, from the homes those index runs kept.

## What is left to the builder

- The docling page-range batch size, and how `by_kind` keeps ascending order (for example, contiguous
  runs per reader).
- The exact page-line format, as long as the status-line protocol and the stderr-tail failure messages
  keep working.
- How the carried heading is injected, as long as the chunk's `heading_path` and embedded text match
  what `contextualize` would produce for a page that had that heading.
- Manifest v2 field names and exact sample sizes, within the numbers above.
- How `model_disk_mb` is measured.
- The pm query-writing and reference-merge prompts.
- The README wording.

## Calls made while writing this up (not discussed)

- The Part 1 gate runs on `bench-suite` with the product branch merged in locally, because `bench/` does
  not exist on `main`.
- `docling_standard` lives until that gate, then it is removed with `docling_paged` and `docling_routed`.
- The text-layer reader is restored with the rest of the router code, but no stack uses it yet.
- If granite-docling does not run on docling 2.115 + mlx-vlm 0.7.12, its stack runs on its own
  interpreter with the docling version it needs (the bench design allows that), and the README records
  that shipping it would also need a docling upgrade. The product's pin is never bumped for the bench.
- Mixed pages are scored against the text layer and reported as a lower bound.
