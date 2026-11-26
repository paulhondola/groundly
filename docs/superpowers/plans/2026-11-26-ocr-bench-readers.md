# OCR Bench Readers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every bench stack becomes a reader of the product's page interface, and docling + RapidOCR, MinerU2.5 and granite-docling are measured on OCR accuracy, index cost and retrieval under a ship rule fixed before any VLM run.

**Architecture:** A stack module exports `SUFFIXES` and `read(path, pages, ocr_lang) -> Iterator[(page_no, DoclingDocument)]`. `bench/stacks/_worker.py` builds the index mode (`--serve`, through the product's `chunk_pages`) and the accuracy mode (`--pages`) from that one function, so scored text and indexed text come from the same page document. A `by_kind` combinator sends `image` pages to a VLM reader and the rest to docling. Manifest v2 adds standalone images, rendered text pages and about 40 hand-checked pages.

**Tech Stack:** Python 3.11+, docling 2.115.0 + RapidOCR, `mineru-vl-utils[mlx]` 2.0.9, `mlx-vlm` 0.7.12 (both from the `bench` extra), pypdfium2, pytest.

**Spec:** `docs/superpowers/specs/2026-11-25-ocr-reader-interface-design.md` (Part 2). Part 1 shipped as PR #32 (`feat/page-reader`), already merged into `bench-suite`.

## Global Constraints

- Work on `bench-suite`, in the `../groundly-bench` worktree. Never commit to `main`; never push `bench-suite` without Paul's OK.
- `groundly/` never imports `bench`. No change to `groundly/` in this plan.
- docling stays at 2.115.0 (`.claude/hooks/guard-pins.sh`). The product's pins are never bumped for the bench; the `bench` extra must not change any package the product uses.
- Course text never enters git: rendered page images, reference text, drafts and page text live under the gitignored `bench/reference/` or in temp files.
- The trust guards run in Groundly's code before any reader: `extract_worker._first_frame` (pixel cap + frame 0) on every image-suffixed input, in both modes.
- Result rows carry numbers and provenance only (`results.append`), and every row records `results.machine()`.
- A verdict uses runs from one session only. The ship rule is committed to `bench/README.md` before any VLM run.
- A reference is never drafted by one candidate engine alone.
- Comments state intent, not history. Slow tests (real models) are marked `@pytest.mark.slow`.

## Review Focus

- A standalone image with several frames in `--pages` mode must be read as frame 0, as the product does: pinned in Task 1.
- `by_kind` on a PDF whose image pages interleave text pages must yield every page once, in ascending order: pinned in Task 4.
- A VLM that loops until its token cap must still yield the page and be counted as capped, not hang or fail the file: pinned in Tasks 2 and 3.
- A rendered page must score against its source PDF page's text layer, never the PNG's (empty) one: pinned in Task 5.
- A non-reference `image` page (no text layer) must be skipped, not scored 0 against an empty reference: pinned in Task 5.

---

### Task 1: The stack contract, the `docling` stack, page-mode timing

**Files:**
- Create: `bench/stacks/docling.py`, `bench/stacks/caps.py`
- Modify: `bench/stacks/_worker.py`, `bench/extract/run.py`, `tests/bench/test_stacks.py`, `tests/bench/test_extract_run.py`
- Delete: `bench/stacks/docling_standard.py`

**Interfaces:**
- Consumes: `extract_worker.docling_pages`, `chunk_pages(pages, on_page)`, `_first_frame`, `_handle(in, out, lang, on_page)`, `EXIT_NO_TEXT`, `IMAGE_SUFFIXES` (`groundly.ingestion.formats`).
- Produces:
  - Stack contract: module attributes `SUFFIXES: frozenset[str]` and `read(path: Path, pages: list[int] | None, ocr_lang: str | None) -> Iterator[tuple[int, DoclingDocument]]`.
  - `bench.stacks.docling`: `SUFFIXES = frozenset({".pdf"}) | IMAGE_SUFFIXES`, `read = extract_worker.docling_pages`.
  - `bench.stacks.caps`: `hit(page: int) -> None` and `drain() -> set[int]` (pages that hit a VLM token cap since the last drain).
  - Page-mode output: one JSONL row per page `{"sha256", "page", "text", "ms", "error", "capped"}`, then one last row `{"meta": {"load_s": float | None}}`.
  - `extract.run.collect(manifest, out_path) -> tuple[list[dict], dict]` (page rows, meta).

- [ ] **Step 1: Write the failing tests** in `tests/bench/test_stacks.py` (replace the `extract`-based fakes with a fake `read`):
  - `test_serve_sends_claimed_suffixes_through_chunk_pages`: a fake stack whose `read` yields two one-page documents; `handler(stack)(pdf, out, None, seen.append)` writes chunks cited to pages 1 and 2 and `seen == [1, 2]`.
  - `test_serve_cuts_an_image_to_its_first_frame_before_read`: a 3-frame TIFF (PIL); the path `read` receives opens with `n_frames == 1`.
  - `test_serve_exits_no_text_when_read_yields_nothing`: `SystemExit` with `EXIT_NO_TEXT`, no out file.
  - Keep `test_other_suffixes_use_groundly_default` (4-argument call).
  - `test_page_mode_times_one_call_per_file_and_kind`: manifest rows for one file, pages 1, 2 (`kind` text) and 3 (`kind` image); the fake `read` records its `pages` argument. Expect calls `[[1], [1, 2], [3]]` (warm-up first), every row's `ms` equal to its call's time over its page count, and a final `{"meta": {"load_s": ...}}` row with `load_s > 0`.
  - `test_page_mode_reads_an_image_as_frame_zero`: a multi-frame TIFF row; `read` receives a single-frame path.
  - `test_page_mode_marks_capped_pages`: the fake `read` calls `caps.hit(2)`; row for page 2 has `"capped": true`, page 1 `false`.
  - Keep the per-file error isolation test, adapted to `read`.
  - In `tests/bench/test_extract_run.py`: `test_collect_splits_the_meta_row` — a file with one page row and a meta row returns `([row], {"load_s": 1.5})`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/bench/test_stacks.py tests/bench/test_extract_run.py -q`
Expected: FAIL (the worker still calls `stack.extract` / `stack.page_texts`; `collect` returns a list).

- [ ] **Step 3: Implement**
  - `_worker.handler(stack)`: a claimed suffix goes through `chunk_pages(stack.read(path, None, ocr_lang), on_page)` with `path = extract_worker._first_frame(in_path)` for image suffixes; no chunk with text → `sys.exit(EXIT_NO_TEXT)`; otherwise write the result. Unclaimed suffixes call `extract_worker._handle(in_path, out_path, ocr_lang, on_page)`.
  - `_worker._page_mode`: group manifest rows by `(path_hint, kind)` in manifest order of first appearance; image-suffixed files pass through `_first_frame` once per file. The first group's first page is read once untimed and its time is `load_s`. Then one timed `read(path, pages, ocr_lang)` per group; `ms` = call time × 1000 / pages in the group; `text` = each yielded document's `export_to_markdown()`; a page the reader did not yield gets `text ""` and `error "page not returned"`. `capped` = page in `caps.drain()` after the call. An exception fails that group's rows with `"{Type}: {msg}"`, as today. A failed warm-up leaves `load_s` `None` and the next group warms up.
  - `bench/extract/run.py`: `collect` returns `(rows, meta)`; metrics gain `load_s` and `capped_pages` (count of rows with `capped`).
  - Delete `docling_standard.py`; its rows stay in `index.jsonl`.

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/bench -q && uv run ruff check bench tests`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add -A bench tests/bench
git commit -m "feat(bench): stacks are readers; the worker builds serve and page modes from read"
```

---

### Task 2: `markdown_page` and the MinerU reader

**Files:**
- Create: `bench/stacks/markdown_page.py`
- Modify: `bench/stacks/mineru.py`
- Test: `tests/bench/test_mineru.py`

**Interfaces:**
- Consumes: the stack contract and `caps.hit` (Task 1).
- Produces:
  - `markdown_page(md: str, n: int) -> DoclingDocument`: a one-page document built with docling's Markdown converter, every item's provenance on page `n`. Empty Markdown gives an empty document with page `n`.
  - `bench.stacks.mineru`: `SUFFIXES = frozenset({".pdf"}) | IMAGE_SUFFIXES`, `read(path, pages, ocr_lang)`, `MAX_TOKENS = 8192`, `RENDER_SCALE = 2`.

- [ ] **Step 1: Write the failing tests** (`MinerUClient` is replaced by a stub through `mineru._get_client`):
  - `test_markdown_page_cites_its_page`: `markdown_page("# T\n\nbody", 7)` → every text item's `prov[0].page_no == 7`, a `SECTION_HEADER` "T".
  - `test_markdown_page_of_nothing_is_an_empty_page`: no texts, `7 in doc.pages`.
  - `test_reads_requested_pdf_pages_in_order`: a 3-page PDF (`tests.ingestion.pdf_fixtures.make_pdf`), `pages=[3, 1]` → yields pages `[1, 3]`.
  - `test_reads_every_page_when_pages_is_none`.
  - `test_a_standalone_image_is_page_one`: a PNG → yields exactly `(1, doc)`.
  - `test_furniture_blocks_are_dropped_and_titles_become_headings`: stub blocks of types `header`, `title`, `text` → markdown has `# <title>` and the text, no header.
  - `test_a_page_that_hits_the_token_cap_is_yielded_and_counted`: the stub reports the cap on page 2 → page 2 still yielded, `caps.drain() == {2}`.
  - Slow: `test_mineru_reads_a_real_page` — one rendered fixture page yields non-empty text.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/bench/test_mineru.py -q`
Expected: FAIL (`markdown_page` missing, `mineru.read` missing).

- [ ] **Step 3: Implement** `markdown_page` from today's `mineru._markdown_document` (docling `DocumentConverter(allowed_formats=[InputFormat.MD])` on a `DocumentStream`), then set each item's provenance page to `n` and add page `n`. Rewrite `mineru.py`: keep `MODEL`, `_SKIPPED`, `_HEADINGS`, `_markdown`; the client is built with `max_new_tokens=MAX_TOKENS` in its sampling params. A PDF page is rendered at `RENDER_SCALE`; an image is opened with PIL as RGB. A page whose generation stops on the token limit calls `caps.hit(n)` (the builder finds where `mineru_vl_utils` reports a length stop; if it reports none, count a block whose output token count reaches `MAX_TOKENS`). Remove `extract`, `page_texts`, `last_ms`.

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/bench/test_mineru.py -q && uv run pytest tests/bench/test_mineru.py -m slow -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bench/stacks/markdown_page.py bench/stacks/mineru.py tests/bench/test_mineru.py
git commit -m "feat(bench): MinerU is a page reader; markdown_page builds its page documents"
```

---

### Task 3: The granite-docling reader

**Files:**
- Create: `bench/stacks/granite.py`
- Test: `tests/bench/test_granite.py`

**Interfaces:**
- Consumes: the stack contract and `caps.hit` (Task 1); `extract_worker.PAGE_BATCH` and `_page_runs`.
- Produces: `bench.stacks.granite`: `SUFFIXES = frozenset({".pdf"}) | IMAGE_SUFFIXES`, `read(path, pages, ocr_lang)`, `MAX_TOKENS = 8192`.

- [ ] **Step 1: Spike (a stop condition).** Convert one page of a fixture PDF with docling's `VlmPipeline` and `vlm_model_specs.GRANITEDOCLING_MLX` on this interpreter.

Expected: a document with text. **If it fails on docling 2.115 + mlx-vlm 0.7.12, stop and report to Paul.** The spec's fallback (granite on its own interpreter with the docling it needs) changes the index bench's argv handling and is Paul's call.

- [ ] **Step 2: Write the failing tests** (the converter replaced by a stub, as in `tests/ingestion/test_pages.py`):
  - `test_reads_every_page_in_page_range_batches`: 20 pages → convert calls `[(1, 16), (17, 20)]`, yields 1…20.
  - `test_requested_pages_are_read_in_contiguous_runs`: `pages=[5, 6, 9]` → calls `[(5, 6), (9, 9)]`.
  - `test_a_page_stopped_by_the_token_limit_is_counted`: the stub's page 3 carries a VLM response with `stop_reason` "length" (or `num_tokens == MAX_TOKENS`) → page 3 yielded, `caps.drain() == {3}`.
  - `test_ocr_lang_is_ignored_without_error`.
  - Slow: `test_granite_reads_a_real_page`.

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/bench/test_granite.py -q`
Expected: FAIL (`bench.stacks.granite` missing).

- [ ] **Step 4: Implement** `granite.py`: one cached `DocumentConverter` with `VlmPipeline`, `vlm_options = GRANITEDOCLING_MLX.model_copy(update={"max_new_tokens": MAX_TOKENS})`, for PDF and image formats; the same batching shape as `docling_pages` (first batch learns `page_count`, `_page_runs` for explicit pages), yielding `doc.filter(page_nrs={n})`. After each conversion, inspect each page's VLM prediction and call `caps.hit(n)` for a length stop.

- [ ] **Step 5: Run them to verify they pass**

Run: `uv run pytest tests/bench/test_granite.py -q && uv run pytest tests/bench/test_granite.py -m slow -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add bench/stacks/granite.py tests/bench/test_granite.py
git commit -m "feat(bench): granite-docling 258M on MLX as a page reader"
```

---

### Task 4: `by_kind` and the combined stacks

**Files:**
- Create: `bench/stacks/by_kind.py`, `bench/stacks/docling_mineru.py`, `bench/stacks/docling_granite.py`
- Test: `tests/bench/test_by_kind.py`

**Interfaces:**
- Consumes: `docling.read`, `mineru.read`, `granite.read`; `text_layer.classify_page`.
- Produces: `by_kind(readers: dict[str, Reader], default: Reader) -> Reader`, where `Reader` is the `read` signature. `docling_mineru.read = by_kind({"image": mineru.read}, default=docling.read)`; `docling_granite` likewise with `granite.read`. Both export `SUFFIXES = frozenset({".pdf"}) | IMAGE_SUFFIXES`.

- [ ] **Step 1: Write the failing tests** (readers are fakes that record their `pages` argument and yield one-page documents):
  - `test_image_pages_go_to_their_reader_and_the_rest_to_default`: a PDF fixture whose pages classify `text, image, text, image` → image reader got `[2, 4]`, default got `[1, 3]`.
  - `test_pages_come_back_once_in_ascending_order`: same PDF → yields `[1, 2, 3, 4]`.
  - `test_a_standalone_image_is_always_image`: a PNG → only the image reader is called, with `[1]`.
  - `test_requested_pages_only`: `pages=[3, 4]` → default `[3]`, image `[4]`.
  - `test_none_means_every_page`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/bench/test_by_kind.py -q`
Expected: FAIL (`bench.stacks.by_kind` missing).

- [ ] **Step 3: Implement** `by_kind`: classify the requested pages with pypdfium2 + `classify_page` (an image suffix is all `image`), split them into contiguous runs that share a reader, call each reader on its run in ascending order and yield as pages arrive. Contiguous runs keep the order without holding finished pages back.

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/bench -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bench/stacks/by_kind.py bench/stacks/docling_mineru.py bench/stacks/docling_granite.py tests/bench/test_by_kind.py
git commit -m "feat(bench): by_kind sends image pages to a VLM reader, the rest to docling"
```

---

### Task 5: Scoring and run metrics for v2

**Files:**
- Modify: `bench/extract/score.py`, `bench/extract/run.py`, `bench/index/run.py`
- Test: `tests/bench/test_score.py`, `tests/bench/test_extract_run.py`, `tests/bench/test_index_run.py`

**Interfaces:**
- Consumes: Task 1's page rows and meta; manifest v2 rows (Task 6), which carry `source` ∈ `pdf | image | rendered`, `kind` (`text | blank | mixed | image` for PDF pages, `standalone` for images, `rendered` for rendered pages), and for rendered rows `render_of: {"sha256", "path_hint", "page"}`.
- Produces: per `kind/lang`: `word_f1`, `bigram_f1`, `word_precision`, `word_recall`, `diacritic_recall`, `diacritic_recall_folded`, `pages/…`, `diacritic_pages/…`. Per run: `load_s`, `ms_p50`, `peak_rss_mb`, `model_disk_mb`, `capped_pages`. Index rows gain `timed_out` (failed files whose message contains "timed out").

- [ ] **Step 1: Write the failing tests:**
  - `test_word_precision_and_recall`: ref "alpha beta gamma", hyp "alpha beta delta epsilon" → precision 0.5, recall 2/3.
  - `test_a_rendered_page_scores_against_its_source_text_layer`: a rendered row whose `render_of` points at a fixture PDF page → `reference_text` returns that page's text layer.
  - `test_a_non_reference_image_page_is_skipped`: an `image` row with `ref: false` → `skipped_pages` 1, no `word_f1/image/…` key.
  - `test_a_standalone_image_scores_only_against_its_reference_file`: `ref: true` with a reference file → scored; without the file → skipped.
  - `test_model_disk_mb_sums_the_arm_repos` (a fake HF cache dir with two repos of known size).
  - `test_index_row_counts_timed_out_files`: a failed list with one "extraction timed out — …" entry and one "parser failed: …" → `timed_out == 1`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/bench/test_score.py tests/bench/test_extract_run.py tests/bench/test_index_run.py -q`
Expected: FAIL on the new tests.

- [ ] **Step 3: Implement.** In `score.py`: precision and recall from the same multiset match as `_f1`; `reference_text` reads a `ref` page's file, a `rendered` page's `render_of` text layer, returns `None` for a non-ref `image` or `standalone` page, else the PDF text layer. In `run.py`: `model_disk_mb(arm: str, cache: Path = HF_HUB_CACHE) -> float | None` sums the on-disk size of the repos listed for the arm in `MODEL_REPOS` (the builder fills the repo ids from the local HF cache: docling's layout/table models, `opendatalab/MinerU2.5-Pro-2605-1.2B`, `ibm-granite/granite-docling-258M-mlx`; RapidOCR's models count for every arm that runs docling). In `index/run.py`: `timed_out` in the row metrics.

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/bench -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bench/extract bench/index tests/bench
git commit -m "feat(bench): precision/recall, rendered and standalone references, model size, timeouts"
```

---

### Task 6: Manifest v2

**Files:**
- Modify: `bench/common/corpus.py`, `bench/extract/sample.py`
- Test: `tests/bench/test_corpus.py`
- Output (gitignored): `bench/reference/rendered/<sha256>-<page>.png`; committed: `bench/corpus/manifest.json` (version 2)

**Interfaces:**
- Consumes: v1 manifest rows; `docling.read` for image language; Task 5's row fields.
- Produces:
  - `image_census(images: list[tuple[str, Path]], read: Reader) -> list[dict]`: one row per standalone image, `kind "standalone"`, `source "image"`, `lang` = `page_lang` of the reader's Markdown.
  - `render(row: dict, out_dir: Path, scale: int = 2) -> dict`: writes the PNG, returns a `rendered` row (`sha256` = the PNG's hash, `path_hint` = the PNG, `page 1`, `render_of`, `render_scale 2`).
  - `sample_v2(v1: dict, pdf_rows, image_rows, out_dir: Path, seed: int = 0) -> list[dict]`.
  - `python -m bench.extract.sample --version 2` writes the v2 manifest.

- [ ] **Step 1: Write the failing tests:**
  - `test_v2_keeps_every_v1_page`: every v1 `(sha256, page)` is in v2 with `source "pdf"`.
  - `test_reference_cells_take_ten_each_where_they_exist`: refs are `{image PDF pages, standalone} × {ro, en}`, at most 10 per cell; a cell with 3 candidates takes 3 and the manifest's `short_cells` names it.
  - `test_mixed_pages_are_not_references_in_v2`.
  - `test_rendered_rows_point_at_their_source`: 50 `text` pages per language where available, `render_scale == 2`, `render_of` matches a census row, the PNG exists under `out_dir`.
  - `test_rendered_images_are_gitignored`: `git check-ignore bench/reference/rendered/x.png` succeeds.
  - `test_image_census_tags_language_from_ocr_text` (fake reader yielding Romanian text → `lang "ro"`).

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/bench/test_corpus.py -q`
Expected: FAIL on the new tests.

- [ ] **Step 3: Implement** the three functions and `--version 2` (the CLI collects PDFs and image-suffixed files from every subject's `materials/`, runs the census, renders, writes the manifest). Seeded sampling keeps it reproducible. Standalone images enter v2 as reference rows only: an image has no text layer, so any other would be skipped at scoring and only cost read time.

- [ ] **Step 4: Run them to verify they pass, then build the manifest**

Run: `uv run pytest tests/bench/test_corpus.py -q && uv run python -m bench.extract.sample --version 2`
Expected: PASS; the CLI prints per-cell counts and names any short cell.

- [ ] **Step 5: Commit** (the manifest holds hashes and flags only)

```bash
git add bench/common/corpus.py bench/extract/sample.py tests/bench/test_corpus.py bench/corpus/manifest.json
git commit -m "feat(bench): manifest v2 — standalone images, rendered text pages, hand-check cells"
```

---

### Task 7: Hand-checked references and the ship rule

**Files:**
- Create: `bench/extract/drafts.py`
- Modify: `bench/README.md`
- Test: `tests/bench/test_drafts.py`
- Output (gitignored): `bench/reference/drafts/<sha256>-<page>.{docling,mineru}.md`, `bench/reference/images/<sha256>-<page>.png`, `bench/reference/<sha256>-<page>.txt`

**Interfaces:**
- Consumes: manifest v2 `ref` rows; `docling.read`, `mineru.read`.
- Produces: `python -m bench.extract.drafts` writes, for every `ref` row, both engines' drafts and the page image (a PDF page rendered at scale 2; a standalone image copied after `_first_frame`).

- [ ] **Step 1: Write the failing test** `test_drafts_writes_both_engines_and_the_image` (fake readers): for one PDF ref row and one image ref row, both `.docling.md` and `.mineru.md` exist and the PNG exists, all under the given reference dir; nothing else is written.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/bench/test_drafts.py -q`
Expected: FAIL (`bench.extract.drafts` missing).

- [ ] **Step 3: Implement `drafts.py`, then run it**

Run: `uv run python -m bench.extract.drafts`
Expected: two drafts and one image per ref row.

- [ ] **Step 4: Merge the references.** For each ref row, read the page image beside both drafts and write the merged text to `bench/reference/<sha256>-<page>.txt`: the words on the page, in reading order, with no Markdown markup, furniture dropped as the drafts drop it. Where the drafts disagree, the image decides.

- [ ] **Step 5: Paul corrects the references (blocking).** Paul checks each `.txt` beside its image in his editor. No VLM run starts before he says they are done.

- [ ] **Step 6: Write the README and the ship rule.** `bench/README.md` gets:
  - the stack contract (`SUFFIXES` + `read`) and both modes; the stacks table: `docling`, `mineru`, `granite`, `text_layer` (census only), `docling_mineru`, `docling_granite`, plus the `product` index arm;
  - manifest v2's sources and reference rules, and that `mixed` scores against the text layer and are a lower bound;
  - the metrics list;
  - the ship rule, verbatim from the spec's "Ship rule" section, (a)–(d) plus "Passing still needs a `/decision`."

- [ ] **Step 7: Commit** (references, drafts and images stay untracked)

```bash
git add bench/extract/drafts.py tests/bench/test_drafts.py bench/README.md
git commit -m "feat(bench): reference drafts, README for readers and manifest v2, ship rule before any VLM run"
```

---

### Task 8: The runs and the verdict

**Files:**
- Output: rows in `bench/results/{extract,index,retrieval}.jsonl` (gitignored); a results summary in this plan's PR description or a message to Paul.

**Interfaces:**
- Consumes: everything above. The ship rule commit from Task 7 must precede the first run (check `git log` for it).

- [ ] **Step 1: Accuracy, one session.**

```bash
uv run python -m bench.extract.run --stack docling
uv run python -m bench.extract.run --stack mineru --session S
uv run python -m bench.extract.run --stack granite --session S
```

Expected: three `extract` rows in session S, each with `scored_pages` > 0 on `image/ro`, `image/en`, `standalone/ro`, `standalone/en`, and `capped_pages` reported.

- [ ] **Step 2: Index, interleaved, in the background.** apd: 5 runs each; then pm: 1 each; then the three files that timed out (`LFTC Cursuri.pdf`, `PI_Slide-uri_3.pdf`, `NT1 - …`) gathered as symlinks in one scratch `materials` dir under a subject `timedout`: 1 each. All with `--keep-homes --timeout 300` (the shipped default, so (c) measures what ships).

```bash
uv run python -m bench.index.run --stacks docling docling_mineru docling_granite --runs 5 --subject apd --input ~/.groundly/apd/materials --keep-homes --timeout 300
```

Expected: rows with `failed_count`, `timed_out` and wall time per arm.

- [ ] **Step 3: Retrieval** on apd and pm from the kept homes, one session per subject, all three arms (`--session` passed as its own argument).

- [ ] **Step 4: Verdict.** Report every metric the ship rule names, per arm, from one session each, and state for each VLM stack which of (a)–(d) hold. Do not propose a product change: passing the rule only opens a `/decision` for Paul.

## What the plan leaves to the builder

- `mineru_vl_utils`'s exact length-stop signal and granite's prediction field for it, within the tests above.
- `MODEL_REPOS` repo ids, read from the local HF cache.
- README wording outside the ship rule.
- The reference-merge wording for each page.
