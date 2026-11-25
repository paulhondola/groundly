# Page Reader (Plan 1: product PR and its retrieval gate) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the PDF page router with one page reader (`docling_pages`) and one page loop
(`chunk_pages`, with heading carry) for every PDF and standalone image, make the extraction timeout
per page, and prove on pm and apd retrieval that nothing got worse before the PR merges.

**Architecture:** A reader yields `(page_no, one-page DoclingDocument)`. The worker's `_handle` feeds
the product reader into `chunk_pages`, which chunks each page, carries the last heading forward and
reports each finished page as a `{"page": n}` line on the worker protocol. `SubprocessExtractor`
restarts its wait on every page line. Plan 2 (the bench refactor and VLM runs) builds on these names.

**Tech Stack:** Python 3.14 venv, docling 2.115.0 + RapidOCR, docling-core 2.105, pytest, ruff,
pyrefly, import-linter.

**Spec:** `docs/superpowers/specs/2026-11-25-ocr-reader-interface-design.md` (Part 1). Read it first.

## Global Constraints

- docling stays pinned at 2.115.0; never bump a guarded pin.
- Product work on branch `feat/page-reader` off `main`; never commit to `main`; Paul merges the PR.
- The worker's stdout is the reply protocol: page lines go through `serve`'s reply stream, never `sys.stdout`.
- The image pixel cap, first-frame extraction and file-size cap run in Groundly's code before any reader.
- Timeout message, exact: `extraction timed out — no page finished within {int(timeout)}s`. `parser failed: …` is unchanged.
- `timeout_seconds` default stays 300. No setting, flag or registry picks the reader.
- `groundly/` never imports `bench`.
- Comments and docstrings state intent and invariants; no history.
- After code changes run `graphify update .` (CLAUDE.md).
- Slow tests are excluded by default (`addopts = "-m 'not slow'"`); run them with `-m slow`.

## Review Focus

- A PDF whose page count is an exact multiple of `PAGE_BATCH` must not trigger a convert whose start is past the last page (docling raises on that). Pinned in Task 1.
- A blank page between titled pages must not become a chunk made of the carried heading alone, and the carry must continue past it. Pinned in Task 2.
- A worker that sends a page line and then dies silently has read the request and must not be resent. Pinned in Task 4.
- A multi-frame TIFF still indexes frame 0 only once images go through the page loop. Pinned in Task 3.
- `ocr_lang` must reach the converter on the new path, or Romanian OCR silently falls back. Pinned in Task 1.

---

### Task 1: Branch, spec commit, and the `docling_pages` reader

**Files:**
- Modify: `groundly/ingestion/extract_worker.py`
- Create: `tests/ingestion/test_pages.py`

**Interfaces:**
- Consumes: `_converter(ocr_lang)`, `_model_step(fn)`, `DOCLING_FORMATS` (existing).
- Produces: `PAGE_BATCH = 16`; `docling_pages(path: Path, pages: list[int] | None, ocr_lang: str | None) -> Iterator[tuple[int, DoclingDocument]]`. `pages=None` means every page. It yields in ascending page order, one `document.filter(page_nrs={n})` per page, including pages with no text.

- [ ] **Step 1: Branch and commit the design and this plan**

```bash
git switch -c feat/page-reader main
git add docs/superpowers/specs/2026-11-25-ocr-reader-interface-design.md docs/superpowers/plans/2026-11-25-page-reader-product.md
git commit -m "docs: OCR reader interface design and Plan 1"
```

Leave the other untracked files (`docs/inference_backends.md`, the torch-engine spec) alone.

- [ ] **Step 2: Write the failing tests in `tests/ingestion/test_pages.py`**

Helpers in the file: `_StubConverter(page_count, init_error=None)` with `initialize_pipeline(fmt)`
(records `fmt`, raises `init_error` if set) and `convert(path, page_range)` (records `page_range`,
returns `SimpleNamespace(document=<one TEXT item per page in the range, clamped to page_count, each with
prov on its page>, input=SimpleNamespace(page_count=page_count))`). An `install` fixture patches
`extract_worker._converter` with `lambda lang: (langs.append(lang), stub)[1]`.

```python
def test_reads_every_page_in_batches(...):       # page_count=20
    assert [n for n, _ in extract_worker.docling_pages(pdf, None, None)] == list(range(1, 21))
    assert stub.calls == [(1, 16), (17, 20)]

def test_each_page_document_holds_only_its_page(...):   # page_count=3
    for n, doc in extract_worker.docling_pages(pdf, None, None):
        assert {t.prov[0].page_no for t in doc.texts} == {n}

def test_page_count_a_multiple_of_the_batch_makes_one_call(...):   # page_count=16
    list(extract_worker.docling_pages(pdf, None, None))
    assert stub.calls == [(1, 16)]

def test_requested_pages_are_read_in_contiguous_runs(...):   # page_count=20
    assert [n for n, _ in extract_worker.docling_pages(pdf, [3, 4, 9], None)] == [3, 4, 9]
    assert stub.calls == [(3, 4), (9, 9)]

def test_ocr_lang_reaches_the_converter(...):
    list(extract_worker.docling_pages(pdf, None, "ro"))
    assert langs and set(langs) == {"ro"}

def test_model_load_failure_is_retryable(...):   # init_error=OSError("couldn't connect")
    with pytest.raises(SystemExit) as exc:
        list(extract_worker.docling_pages(pdf, None, None))
    assert exc.value.code == extract_worker.EXIT_MODEL_UNAVAILABLE
    assert stub.calls == []

def test_an_image_is_one_page_on_the_image_pipeline(...):   # path slide.png, page_count=1
    assert [n for n, _ in extract_worker.docling_pages(png, None, None)] == [1]
    assert stub.formats == [InputFormat.IMAGE]
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/ingestion/test_pages.py -q`
Expected: FAIL, `AttributeError: module ... has no attribute 'docling_pages'`.

- [ ] **Step 4: Implement `PAGE_BATCH` and `docling_pages` in `extract_worker.py`**

Input format from `DOCLING_FORMATS[path.suffix.lower()]`. Initialize the pipeline through
`_model_step` before any convert. For `pages=None`: convert `(1, PAGE_BATCH)` first, read the total from
`result.input.page_count`, then continue in `PAGE_BATCH` ranges up to the total, never starting past it.
For a requested list: sorted contiguous runs, each capped at `PAGE_BATCH` pages. Yield each page of a
converted range as soon as that range is converted.

- [ ] **Step 5: Run them to see them pass**

Run: `uv run pytest tests/ingestion/test_pages.py -q`
Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
git add groundly/ingestion/extract_worker.py tests/ingestion/test_pages.py
git commit -m "feat(ingestion): docling_pages reads a document page by page in batches"
```

---

### Task 2: `chunk_pages` with heading carry

**Files:**
- Modify: `groundly/ingestion/extract_worker.py`
- Test: `tests/ingestion/test_pages.py`

**Interfaces:**
- Consumes: `chunk_document(doc, page)` (existing).
- Produces: `_no_progress(n: int) -> None`; `last_heading(doc: DoclingDocument) -> str | None` (moved verbatim from `pdf_router.py`); `chunk_pages(pages: Iterable[tuple[int, DoclingDocument]], on_page: Callable[[int], None] = _no_progress) -> dict` returning `{"pages": <pages consumed>, "chunks": [...]}`.

- [ ] **Step 1: Write the failing tests**

Copy `_fake_chunk_document` and `_docling_doc(page_no, text=..., header=None)` from
`tests/ingestion/test_pdf_router.py` into `test_pages.py`. The fake chunk_document returns one chunk per
document with text, its `heading_path` being the last `SECTION_HEADER` in the document. Patch it onto
`extract_worker.chunk_document`.

```python
def test_untitled_page_carries_the_previous_heading():
    pages = [(1, _docling_doc(1, header="Deadlock")), (2, _docling_doc(2))]
    assert [c["heading_path"] for c in extract_worker.chunk_pages(pages)["chunks"]] == ["Deadlock", "Deadlock"]

def test_first_untitled_page_has_no_heading():
    pages = [(1, _docling_doc(1)), (2, _docling_doc(2, header="Later"))]
    assert [c["heading_path"] for c in extract_worker.chunk_pages(pages)["chunks"]] == [None, "Later"]

def test_a_page_with_its_own_heading_keeps_it():
    pages = [(1, _docling_doc(1, header="Deadlock")), (2, _docling_doc(2, header="Scheduling"))]
    assert [c["heading_path"] for c in extract_worker.chunk_pages(pages)["chunks"]] == ["Deadlock", "Scheduling"]

def test_blank_page_gets_no_chunk_and_the_carry_continues():
    pages = [(1, _docling_doc(1, header="Deadlock")), (2, _docling_doc(2, text=None)), (3, _docling_doc(3))]
    result = extract_worker.chunk_pages(pages)
    assert result["pages"] == 3
    assert [(c["page"], c["heading_path"]) for c in result["chunks"]] == [(1, "Deadlock"), (3, "Deadlock")]

def test_on_page_reports_every_page_in_order():
    seen = []
    extract_worker.chunk_pages([(n, _docling_doc(n, text=None if n == 2 else "x")) for n in (1, 2, 3)], seen.append)
    assert seen == [1, 2, 3]

@pytest.mark.slow
def test_carried_heading_is_embedded_like_contextualize():   # real chunk_document, no patch
    pages = [(1, _docling_doc(1, text="Mutual exclusion is required.", header="Deadlock")),
             (2, _docling_doc(2, text="Circular wait occurs among processes."))]
    page2 = [c for c in extract_worker.chunk_pages(pages)["chunks"] if c["page"] == 2]
    assert page2[0]["heading_path"] == "Deadlock"
    assert page2[0]["text"].startswith("Deadlock\n")
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/ingestion/test_pages.py -q`
Expected: the 5 fast new tests FAIL with `AttributeError: ... 'chunk_pages'`.

- [ ] **Step 3: Implement `_no_progress`, `last_heading` and `chunk_pages` in `extract_worker.py`**

Per page, in order: if a heading is carried, the page has text, and the page's first top-level body
item is not a section header or title, insert the carried heading before that item with
`doc.insert_heading(sibling=…, text=carried, level=1, after=False)`, so `HybridChunker` treats it as the
page's heading. Then `chunk_document(doc, page=n)`, then `carried = last_heading(doc) or carried`, then
`on_page(n)`. A page with no text is never given the heading.

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest tests/ingestion/test_pages.py -q && uv run pytest tests/ingestion/test_pages.py -q -m slow -k contextualize`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add groundly/ingestion/extract_worker.py tests/ingestion/test_pages.py
git commit -m "feat(ingestion): chunk_pages chunks each page and carries the last heading forward"
```

---

### Task 3: Route PDFs and images through the page loop; remove the router

**Files:**
- Modify: `groundly/ingestion/extract_worker.py` (`_handle`, `_extract_docling`, module docstring)
- Delete: `groundly/ingestion/pdf_router.py`, `tests/ingestion/test_pdf_router.py`
- Modify: `tests/ingestion/test_extract_worker.py`

**Interfaces:**
- Consumes: `docling_pages`, `chunk_pages`, `_no_progress` (Tasks 1–2), `_first_frame` (existing).
- Produces: `_handle(in_path: Path, out_path: Path, ocr_lang: str | None, on_page: Callable[[int], None] = _no_progress) -> None`.

- [ ] **Step 1: Write the failing test and convert the router-era tests in `test_extract_worker.py`**

New fast test:

```python
@pytest.mark.parametrize("name", ["deck.pdf", "slide.png"])
def test_handle_reads_pdfs_and_images_page_by_page(tmp_path, monkeypatch, name):
    # docling_pages patched to record (path.name, pages, lang) and yield one page with text;
    # chunk_document patched to the one-chunk fake. A real 1-frame PNG / PDF fixture is written first.
    seen = []
    extract_worker._handle(src, tmp_path / "out.json", "ro", seen.append)
    assert recorded == [(name, None, "ro")]
    assert json.loads((tmp_path / "out.json").read_text())["chunks"][0]["page"] == 1
    assert seen == [1]
```

Convert the slow tests to the product path with a helper
`_via_handle(path, tmp_path, ocr_lang=None) -> dict` that calls `extract_worker._handle` and loads
`out.json`:
- `test_scanned_pdf_indexes_via_ocr`, `test_scanned_pdf_romanian_diacritics_via_ocr_lang` (`ocr_lang="ro"`), `test_standalone_image_indexes_via_ocr`, `test_title_only_image_salvages_heading_text` and `test_multi_frame_image_indexes_first_frame_only` (keep `pages == 1` and no second-frame text): `_extract_docling(x)` becomes `_via_handle(x, tmp_path)`. The assertions stay.
- `test_landscape_deck_routes_pages_and_cites_each` becomes `test_deck_pages_each_cite_their_own_page`, on the same 4-page fixture through `_via_handle`: `pages == 4`, chunk pages `== {1, 2, 3, 4}`, `"Circular wait"` on page 3, `"Deadlock conditions"` on page 1, `"Scheduling"` on page 2.
- Delete `test_blank_landscape_deck_exits_no_text`. `test_blank_pdf_exits_no_text` already covers a blank PDF.

- [ ] **Step 2: Run to see the new test fail**

Run: `uv run pytest tests/ingestion/test_extract_worker.py -q -k page_by_page`
Expected: FAIL (`_handle` takes 3 arguments, or routes the PDF to `pdf_router`).

- [ ] **Step 3: Implement**

In `_handle`: `.pdf` and `IMAGE_SUFFIXES` go to
`chunk_pages(docling_pages(path, None, ocr_lang), on_page)`, with an image passed through
`_first_frame` first. `DOCLING_SUFFIXES` and notebooks keep their path, and the no-text exit stays.
Remove the image branch from `_extract_docling`, which no longer receives images. Rewrite the
module docstring's routing sentence for the page loop. Delete `pdf_router.py` and
`test_pdf_router.py`.

- [ ] **Step 4: Run the ingestion tests, fast and the converted slow ones**

Run: `uv run pytest tests/ingestion -q && uv run pytest tests/ingestion/test_extract_worker.py -q -m slow`
Expected: all pass. `grep -rn pdf_router groundly tests` prints nothing.

- [ ] **Step 5: Commit**

```bash
git add -A groundly/ingestion tests/ingestion
git commit -m "feat(ingestion): every PDF and image is read and chunked page by page; drop the page router"
```

---

### Task 4: Page progress lines and the per-page timeout

**Files:**
- Modify: `groundly/ingestion/extract_worker.py` (`serve`), `groundly/ingestion/extract.py` (`_exchange`), `groundly/core/config.py` (the `timeout_seconds` comment at line 22 and the template comment at line 129)
- Modify: `tests/ingestion/stub_worker.py`, `tests/ingestion/test_extract_session.py`, `tests/ingestion/test_extract_worker_serve.py`

**Interfaces:**
- Consumes: `_handle(..., on_page)` (Task 3).
- Produces: protocol per request: zero or more `{"page": n}` lines, then exactly one `{"status": …}` line. `serve(stdin, stdout, handle)` calls `handle(in_path, out_path, ocr_lang, on_page)`, where `on_page` writes and flushes a page line on `stdout`.

- [ ] **Step 1: Write the failing tests**

`test_extract_worker_serve.py`: handlers take `(i, o, lang, on_page)`. `_run` returns the `status`
values only.

```python
def test_page_lines_precede_the_status_line():
    out = io.StringIO()
    w.serve(io.StringIO(req), out, handle=lambda i, o, lang, on_page: (on_page(1), on_page(2)))
    assert [json.loads(l) for l in out.getvalue().splitlines()] == [{"page": 1}, {"page": 2}, {"status": "ok"}]
```

In `test_library_stdout_never_reaches_the_protocol_stream`, the handler also calls `on_page(1)`.
Assert the stdout lines equal `[{"page": 1}, {"status": "ok"}]`.

`stub_worker.py` gains three behaviours, keyed by filename prefix as the others are:
- `slowpages`: three page lines 0.6 s apart, then ok.
- `pagehang`: one page line, then a 60 s sleep.
- `pagedie`: append a line to `req["in"] + ".seen"`, send one page line, then `os._exit(9)` with nothing on stderr.

`test_extract_session.py`:

```python
def test_pages_that_each_finish_in_time_never_time_out(tmp_path):   # slowpages.pdf, timeout=1
    with _ex(timeout=1) as ex:
        assert ex.extract(tmp_path / "slowpages.pdf").chunks

def test_a_stalled_page_names_the_per_page_limit_and_the_next_file_indexes(tmp_path):   # pagehang.pdf, ok.txt
    with _ex(timeout=1) as ex:
        with pytest.raises(ExtractionFailure, match="^extraction timed out — no page finished within 1s$"):
            ex.extract(tmp_path / "pagehang.pdf")
        assert ex.extract(tmp_path / "ok.txt").chunks

def test_a_worker_that_dies_after_a_page_is_not_resent(tmp_path):   # pagedie.pdf
    with _ex() as ex, pytest.raises(ExtractionFailure, match="^parser failed: exit code 9$"):
        ex.extract(tmp_path / "pagedie.pdf")
    assert len((tmp_path / "pagedie.pdf.seen").read_text().splitlines()) == 1
```

The existing `test_a_timeout_names_the_limit_and_the_next_file_still_indexes` changes its `match` to
`"^extraction timed out — no page finished within 1s$"`.

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/ingestion/test_extract_session.py tests/ingestion/test_extract_worker_serve.py -q`
Expected: the new tests and the reworded one FAIL.

- [ ] **Step 3: Implement**

`serve` builds `on_page` over its `stdout` argument and passes it to `handle`. `_exchange` loops on
`self._replies.get(timeout=self.timeout)`. Each page line restarts the wait and marks that the worker
read the request. The first status line is returned. On a death, it raises `parser failed` without a
resend once any page line has arrived. The timeout message is per Global Constraints. The
`timeout_seconds` comments say: seconds without a finished page, or the whole file for formats read in
one piece.

- [ ] **Step 4: Run to see them pass**

Run: `uv run pytest tests/ingestion -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add groundly/ingestion groundly/core/config.py tests/ingestion
git commit -m "feat(ingestion): the extraction timeout restarts on every finished page"
```

---

### Task 5: Decision 49 revision, full verification, review

**Files:**
- Modify, through `/decision`: `docs/groundly-spec.md` (UC-01 line 28, §4 Extraction row line 94, decision 49 line 182), `docs/tech-stack/tech-stack.md:11`, `docs/architecture/retrieval.md:41`, `docs/infrastructure/security.md:63-64`, `docs/use-cases/knowledge-base.md` (timeout wording, if any), `.claude/rules/architecture.md:22` (timeout message string).

- [ ] **Step 1: Run `/decision` with this decision text**

> Decision 49 revised (Paul, 2026-11-25; design in `superpowers/specs/2026-11-25-ocr-reader-interface-design.md`): no page router. Every PDF and standalone image is read page by page by docling + RapidOCR (`docling_pages`, 16-page batches) and chunked per page (`chunk_pages`). A chunk with no heading of its own carries the last heading from an earlier page. `ingestion.timeout_seconds` (default 300) means no page finished within N s; formats read in one piece wait for the whole file. The timeout message is `extraction timed out — no page finished within Ns`. The worker no longer opens PDFs with pypdfium2 to classify pages (docling's own backend still renders through it).

- [ ] **Step 2: Check that no stale references remain**

Run: `grep -rn -e pdf_router -e "page router" -e "timed out after" groundly tests docs .claude/rules --exclude-dir=superpowers`
Expected: only the decision register's own revision line.

- [ ] **Step 3: Run the full verification**

Run: `uv run pytest -q && uv run pytest -q -m slow tests/ingestion && uv run ruff check . && uv run ruff format --check . && uv run pyrefly check && uv run lint-imports`
Expected: every command exits 0.

- [ ] **Step 4: Refresh the graph and review**

Run `graphify update .`. Then run the `spec-guardian` and `security-reviewer` agents on
`git diff main...feat/page-reader`. Fix what they confirm and rerun Step 3.

- [ ] **Step 5: Commit, but do not push yet; the gate comes first**

```bash
git add -A docs .claude/rules groundly tests
git commit -m "docs: decision 49 revised — page reader, heading carry, per-page timeout"
```

---

### Task 6: Bring the product branch into bench-suite

**Files:**
- Create: `bench/stacks/text_layer.py`, `tests/bench/test_text_layer.py`
- Modify: `bench/common/corpus.py` (its `pdf_router` import), `bench/stacks/_worker.py` (`handler`), `bench/index/run.py` (`worker_argv`, `_one`, `main`), `tests/bench/test_stacks.py`, `tests/bench/test_index_run.py`, `bench/README.md`
- Delete: `bench/stacks/docling_routed.py`, `bench/stacks/docling_paged.py`, and their tests

**Interfaces:**
- Consumes: `_handle(..., on_page)` and the protocol from Task 4.
- Produces: `bench.stacks.text_layer` exposing `classify_page`, `is_slide_deck`, `page_lines`, `split_title`, `text_page_document` and `PageClass` (Plan 2's `by_kind` and the census use them); index-bench arm `product`; index-bench flag `--timeout SECONDS`.

- [ ] **Step 1: Merge**

```bash
git switch bench-suite && git merge --no-ff feat/page-reader
```

Expected: it merges cleanly or with trivial conflicts. Never merge into `main`.

- [ ] **Step 2: Write the failing tests**

- `tests/bench/test_text_layer.py`: the tests from `git show main:tests/ingestion/test_pdf_router.py` that do not call `pdf_router.extract`, with `pdf_router` replaced by `text_layer`.
- `tests/bench/test_index_run.py`:

```python
def test_product_arm_runs_the_shipped_worker():
    assert index_run.worker_argv("product", None)[1:] == ["-m", "groundly.ingestion.extract_worker", "--serve"]
```

- `tests/bench/test_stacks.py`: the handler tests call `handle(src, out, None, seen.append)`. Drop the routed and paged cases.

- [ ] **Step 3: Implement**

- `git show main:groundly/ingestion/pdf_router.py > bench/stacks/text_layer.py`, minus `extract` and `last_heading`. Its docstring says it holds the text-layer reading and page classification for the census and the `by_kind` combinator.
- Repoint `bench/common/corpus.py` to `bench.stacks.text_layer`.
- `_worker.handler` returns `handle(in_path, out_path, ocr_lang, on_page)`. Files claimed by the stack go to `stack.extract` (only `docling_standard` still has it). Every other file goes to `extract_worker._handle(..., on_page)`.
- `worker_argv("product", …)` runs `-m groundly.ingestion.extract_worker --serve`.
- `--timeout` reaches `_one`, which builds `load_settings().ingestion.model_copy(update={"timeout_seconds": t})`, and the row records `"timeout_s"`.
- Delete the routed and paged stacks. The README stack table lists `docling_standard`, `product` and `mineru`, and notes that routed and paged were retired with their rows kept in `index.jsonl`.

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/bench tests/ingestion -q && uv run ruff check bench tests && grep -rn pdf_router bench tests`
Expected: the tests pass and the grep prints nothing.

- [ ] **Step 5: Commit**

```bash
git add -A bench tests
git commit -m "chore(bench): take the page reader; router code moves to bench/stacks/text_layer, product arm, --timeout"
```

---

### Task 7: pm queries and the retrieval gate

**Files:**
- Create: `bench/retrieval/queries/pm.json`

- [ ] **Step 1: Write about 30 Romanian queries on pm**

Each entry is `{"query", "file", "page"}`, the same format as `apd.json`. `file` is the material's
filename as stored in `~/.groundly/pm/materials`. The queries span at least 10 files, mostly the
non-deck PDFs, with one query per page. Each answer must be readable on that page's text layer. The
queries are in Romanian and phrased like `apd.json`'s.

- [ ] **Step 2: Paul spot-checks (blocking)**

Show Paul 10 queries, each with its page text. Fix any he rejects.

- [ ] **Step 3: Index both arms on pm and apd, homes kept (run in the background; pm is long)**

```bash
uv run python -m bench.index.run --stacks docling_standard product --runs 1 --subject pm --input ~/.groundly/pm/materials --keep-homes --timeout 3600
uv run python -m bench.index.run --stacks docling_standard product --runs 1 --subject apd --input ~/.groundly/apd/materials --keep-homes --timeout 3600
```

The timeout is 3600 s for both arms because the gate measures chunking, not timeouts. Record the kept
home paths printed.

- [ ] **Step 4: Retrieval, one session per subject**

For each subject, run both arms with the same `--session`. The first run prints the session id.

```bash
uv run python -m bench.retrieval.run --home <home> --subject pm --queries bench/retrieval/queries/pm.json --arm <arm> [--session S]
uv run python -m bench.common.compare retrieval --baseline docling_standard --metric hit_within1@8
```

Do the same for apd with `apd.json`.

- [ ] **Step 5: Verdict**

The gate passes when `product`'s `hit_within1@8` is at least `docling_standard`'s on both pm and apd.
Report `hit_exact@8` and `mrr_within1@8` beside it.
- **Pass:** commit `pm.json` on `bench-suite`. Push `feat/page-reader` and open its PR to `main` with the gate numbers in the description. Paul merges.
- **Fail:** stop. Report the per-query differences to Paul and open no PR.

```bash
git add bench/retrieval/queries/pm.json && git commit -m "feat(bench): pm Romanian retrieval queries"
```
