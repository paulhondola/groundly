# PDF Page Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Slide-deck PDFs index their text-layer pages straight from pypdfium2 and send only mixed/image pages to docling, one page at a time, so every chunk cites its own page.

**Architecture:** A new worker-side module `groundly/ingestion/pdf_router.py` classifies each page of a landscape-majority PDF, builds a one-page `DoclingDocument` for text/blank pages, converts mixed/image pages with the existing `_converter(ocr_lang)` and `page_range=(n, n)`, and chunks every page with the same `HybridChunker` through one shared helper. `_handle` sends `.pdf` to it; portrait PDFs and files pypdfium2 cannot open go to `_extract_docling` whole, as today. The worker's output contract and the parent side are unchanged.

**Tech Stack:** Python 3.11, pypdfium2 5.14.0 (raw pdfium bindings), docling 2.115 / docling-core, HybridChunker with the pinned bge-m3 tokenizer, pytest.

**Spec:** [`docs/superpowers/specs/2026-11-20-pdf-page-router-design.md`](../specs/2026-11-20-pdf-page-router-design.md)

## Global Constraints

- Thresholds are module constants in `pdf_router.py`: `MIN_CHARS = 40`, `IMG_COVER = 0.05`. `chars` = stripped text-layer length; `cover` = summed on-page image-object area / page area, capped at 1.0.
- Page classes: text = chars ≥ 40 and cover < 0.05; blank = chars < 40 and cover < 0.05 (both text route); mixed = chars ≥ 40 and cover ≥ 0.05; image = chars < 40 and cover ≥ 0.05 (both docling route).
- Slide deck = more than half of the pages are landscape (width ≥ height). Otherwise the whole file goes to `_extract_docling`.
- Slide title = the leading run of lines whose median font size is ≥ 1.15 × the page's body size and that hold ≥ 3 letters; body size = character-weighted modal font size (rounded). Nothing after the first body line is a heading.
- **No Markdown is generated or parsed** on the text route. Chunk text stays verbatim page text plus the heading `contextualize` prepends.
- Worker output stays `{"pages": N, "chunks": [{"text", "heading_path", "page", "token_count"}]}`; `pages` is the PDF's page count; chunks are in page order.
- torch is never imported by the parent or any long-running process; `pdf_router` is imported only inside the worker (`_handle`).
- Failure messages and exit codes stay the four named ones (`EXIT_NO_TEXT`, `EXIT_MODEL_UNAVAILABLE`, `EXIT_INPUT_TOO_LARGE`, crash/timeout handled by the parent). Model load stays inside `_model_step`.
- pypdfium2 is pinned exactly at `5.14.0` (what docling 2.115 resolves today). Adding it to `.claude/hooks/guard-pins.sh` is Paul's open call — this plan does not touch the hook.
- `Chunking.strategy` default becomes `"page-routed-hybrid"`; no `format_version` bump.
- Comments state intent and invariants, never history (`.claude/rules/conventions.md`). Never commit on `main`; work on `pdf-page-router`.

## Review Focus

1. **A PDF pypdfium2 cannot open** (corrupt bytes, encrypted): the student should see docling's failure message, exactly as today — never a pypdfium2 traceback. Test in Task 3.
2. **docling's models fail to load on the first mixed/image page** (offline, cold cache): the file must exit `EXIT_MODEL_UNAVAILABLE` (retryable), not be recorded as a bad document. Test in Task 3.
3. **A deck whose only image page OCRs to nothing**: its text pages still index; the empty page contributes no chunks and no exit. Test in Task 3.
4. **A deck whose first page has no title**: there is nothing to carry forward, so its chunks have `heading_path = None` (no crash, no empty-string heading). Test in Task 3.
5. **Exactly half landscape pages**: not a majority, so docling takes the whole file. Test in Task 1.

---

### Task 1: Dependency, PDF fixtures, page classification

**Files:**
- Modify: `pyproject.toml` (dependencies list), `uv.lock`
- Create: `groundly/ingestion/pdf_router.py`
- Create: `tests/ingestion/pdf_fixtures.py`
- Create: `tests/ingestion/test_pdf_router.py`

**Interfaces:**
- Produces: `pdf_router.MIN_CHARS: int = 40`, `pdf_router.IMG_COVER: float = 0.05`, `pdf_router.classify_page(page: pypdfium2.PdfPage) -> Literal["text", "blank", "mixed", "image"]`, `pdf_router.is_slide_deck(pdf: pypdfium2.PdfDocument) -> bool`.
- Produces (tests): `pdf_fixtures.Page` dataclass `(width: float = 960, height: float = 540, lines: list[tuple[str, float]] = [], image_cover: float = 0.0)` — `lines` are `(text, font_size)` top to bottom; `pdf_fixtures.make_pdf(path: Path, pages: list[Page]) -> Path`.

- [ ] **Step 1: Add pypdfium2 as a direct dependency**

Insert `"pypdfium2==5.14.0",            # PDF text layer + page classification (pdf_router.py)` on its own line right after the `"onnxruntime>=1.31",` line in `pyproject.toml`. Use an Edit whose `old_string` is only the onnxruntime line — an `old_string` containing `docling==`/`rapidocr==`/`mlx==` trips `.claude/hooks/guard-pins.sh`. Run `uv lock` and confirm `uv.lock` still resolves pypdfium2 5.14.0 (`grep -n 'pypdfium2-5.14.0.tar.gz' uv.lock`).

- [ ] **Step 2: Write the fixture builder** in `tests/ingestion/pdf_fixtures.py`

No reportlab/fpdf in the venv; pypdfium2 writes text-layer PDFs itself. Verified recipe (font sizes and `\r\n` line breaks read back exactly):

```python
def _text(pdf, page, text, size, x, y):
    obj = raw.FPDFPageObj_NewTextObj(pdf.raw, b"Helvetica", ctypes.c_float(size))
    buf = ctypes.create_string_buffer((text + "\0").encode("utf-16-le"))
    raw.FPDFText_SetText(obj, ctypes.cast(buf, ctypes.POINTER(raw.FPDF_WCHAR)))
    raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, x, y)
    raw.FPDFPage_InsertObject(page.raw, obj)


# image covering `image_cover` of the page, placed bottom-right:
img = pdfium.PdfImage.new(pdf)
img.set_bitmap(pdfium.PdfBitmap.from_pil(Image.new("RGB", (64, 64), "red")))
img.set_matrix(pdfium.PdfMatrix().scale(w_img, h_img).translate(x, y))
page.insert_obj(img)
page.gen_content()
```

`make_pdf` lays lines from the top (y = height − 60, step 1.6 × size), x = 40, builds every page, saves to `path`, returns `path`.

- [ ] **Step 3: Write the failing tests** in `tests/ingestion/test_pdf_router.py`

```python
@pytest.mark.parametrize(("lines", "cover", "expected"), [
    ([("Mutual exclusion and circular wait are required.", 16)], 0.0, "text"),
    ([("Deadlock", 32)], 0.0, "blank"),
    ([("Mutual exclusion and circular wait are required.", 16)], 0.10, "mixed"),
    ([], 0.5, "image"),
    ([("Mutual exclusion and circular wait are required.", 16)], 0.04, "text"),  # below 5 %
])
def test_classify_page(tmp_path, lines, cover, expected):
    pdf = pdfium.PdfDocument(make_pdf(tmp_path / "d.pdf", [Page(lines=lines, image_cover=cover)]))
    assert pdf_router.classify_page(pdf[0]) == expected

def test_landscape_majority_is_a_slide_deck(tmp_path):  # 2 landscape + 1 portrait → True
def test_portrait_majority_is_not_a_slide_deck(tmp_path):  # 1 landscape + 2 portrait → False
def test_exactly_half_landscape_is_not_a_slide_deck(tmp_path):  # 1 + 1 → False
```

Portrait page = `Page(width=540, height=960, ...)`.

- [ ] **Step 4: Run to verify they fail**

Run: `uv run pytest tests/ingestion/test_pdf_router.py -v`
Expected: FAIL — `ModuleNotFoundError: groundly.ingestion.pdf_router` (or `AttributeError`).

- [ ] **Step 5: Implement `classify_page` and `is_slide_deck`** in `groundly/ingestion/pdf_router.py`

Module docstring: worker-only; what the router does; that `IMG_COVER` equals docling 2.115's `bitmap_area_threshold`, so any page docling would OCR keeps docling's route. `chars` from `page.get_textpage().get_text_range().strip()`; `cover` from `page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE])` → `obj.get_bounds()` clipped to the page box, summed, divided by page area, capped at 1.0 (same as the spike's `build_router.classify`). Landscape = `width >= height` from `page.get_size()`.

- [ ] **Step 6: Run to verify they pass**

Run: `uv run pytest tests/ingestion/test_pdf_router.py -v`
Expected: PASS (8 tests).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock groundly/ingestion/pdf_router.py tests/ingestion/pdf_fixtures.py tests/ingestion/test_pdf_router.py
git commit -m "feat(ingestion): classify PDF pages for the page router"
```

---

### Task 2: Text route — lines, slide title, one-page DoclingDocument

**Files:**
- Modify: `groundly/ingestion/pdf_router.py`
- Test: `tests/ingestion/test_pdf_router.py`

**Interfaces:**
- Consumes: `pdf_fixtures.make_pdf`, `Page` (Task 1).
- Produces: `pdf_router.page_lines(page: pypdfium2.PdfPage) -> list[tuple[str, float]]` — non-empty stripped visual lines with each line's median font size over non-space characters; `pdf_router.split_title(lines: list[tuple[str, float]]) -> tuple[str | None, list[str]]` — `(title joined with " " or None, body lines)`; `pdf_router.text_page_document(page_no: int, width: float, height: float, title: str | None, body: list[str], carried: str | None) -> DoclingDocument`.

- [ ] **Step 1: Write the failing tests**

```python
def test_page_lines_reads_text_and_sizes(tmp_path):
    # Page(lines=[("Deadlock conditions", 32), ("Mutual exclusion is required.", 16)])
    assert pdf_router.page_lines(pdf[0]) == [
        ("Deadlock conditions", 32.0),
        ("Mutual exclusion is required.", 16.0),
    ]


def test_soft_hyphen_marker_is_dropped(tmp_path):
    # Page(lines=[("using only send­receive, BUT", 16)]) — pdfium reports the marker as 0x02
    # (FPDFText_GetUnicode) / U+FFFE (get_text_range); docling 2.115 drops it ("sendreceive"
    # in today's apd index, Curs 10 page 4), so the text route does too.
    assert pdf_router.page_lines(pdf[0])[0][0] == "using only sendreceive, BUT"


def test_split_title_takes_the_leading_large_run():
    assert pdf_router.split_title(
        [("Deadlock", 32), ("conditions", 30), ("Body text here", 16), ("Big again", 32)]
    ) == ("Deadlock conditions", ["Body text here", "Big again"])


def test_split_title_needs_three_letters():
    assert pdf_router.split_title([("12", 32), ("Body text here", 16)]) == (
        None,
        ["12", "Body text here"],
    )


def test_split_title_none_when_all_one_size():
    assert pdf_router.split_title([("a b c", 16), ("d e f", 16)]) == (None, ["a b c", "d e f"])


def test_code_line_with_hash_stays_verbatim_body():
    doc = pdf_router.text_page_document(
        3, 960, 540, "Includes", ["#include <stdio.h>"], carried=None
    )
    assert [t.text for t in doc.texts if t.label == DocItemLabel.TEXT] == ["#include <stdio.h>"]
    assert [t.text for t in doc.texts if t.label == DocItemLabel.SECTION_HEADER] == ["Includes"]


def test_untitled_page_carries_the_earlier_title():
    doc = pdf_router.text_page_document(4, 960, 540, None, ["more detail"], carried="Deadlock")
    assert [t.text for t in doc.texts if t.label == DocItemLabel.SECTION_HEADER] == ["Deadlock"]


def test_every_item_cites_its_page():
    doc = pdf_router.text_page_document(7, 960, 540, "T", ["a", "b"], carried=None)
    assert {t.prov[0].page_no for t in doc.texts} == {7}
```

The soft-hyphen fixture: if Helvetica text with U+00AD does not read back as 0x02 from pdfium, write the marker test against `page_lines` fed through a monkeypatched character source instead — the assertion (marker dropped, words joined) is what matters.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/ingestion/test_pdf_router.py -k "lines or soft or title or hash or carries or cites" -v`
Expected: FAIL with `AttributeError` on the missing functions.

- [ ] **Step 3: Implement the three functions**

- `page_lines`: iterate `raw.FPDFText_GetUnicode(tp.raw, i)`; `\r`/`\n` end a line; drop `0x02`, `0xFFFE` and `0x00AD`; font size from `raw.FPDFText_GetFontSize` for non-space characters only (spaces report size 1). Same loop as the spike's `page_markdown` minus the Markdown.
- `split_title`: body size = `Counter` keyed by `round(size)` weighted by `len(text)`, most common; then the spike's leading-run rule with the constants above.
- `text_page_document`: `DoclingDocument(name=f"page-{page_no}")`, `add_page(page_no=page_no, size=Size(width, height))`; one `ProvenanceItem(page_no=page_no, bbox=BoundingBox(l=0, t=height, r=width, b=0), charspan=(0, 0))` shared by every item (whole-page box: the builder's call per spec, nothing reads it); `add_heading(title or carried, level=1, prov=…)` when either is set (own title wins); each body line `add_text(DocItemLabel.TEXT, line, prov=…)`.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/ingestion/test_pdf_router.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add groundly/ingestion/pdf_router.py tests/ingestion/test_pdf_router.py
git commit -m "feat(ingestion): text route builds a one-page DoclingDocument with slide titles"
```

---

### Task 3: Router assembly, docling route, shared chunking, wiring

**Files:**
- Modify: `groundly/ingestion/extract_worker.py` (`_extract_docling` chunk loop → shared helper; `_handle` PDF branch)
- Modify: `groundly/ingestion/pdf_router.py`
- Test: `tests/ingestion/test_pdf_router.py`, `tests/ingestion/test_extract_worker.py` (slow test)

**Interfaces:**
- Consumes: Tasks 1–2; `extract_worker._converter(ocr_lang)`, `extract_worker._model_step(fn)`, `extract_worker._extract_docling(path, ocr_lang)`.
- Produces: `extract_worker.chunk_document(doc: DoclingDocument, page: int | None = None) -> list[dict]` — today's chunk loop plus title-only salvage, moved verbatim out of `_extract_docling`; a non-`None` `page` overrides every chunk's page. Tokenizer and `HybridChunker` come from a new `@functools.cache` `_chunker()` returning `(tokenizer, chunker)` (tokenizer load stays in `_model_step`). `pdf_router.extract(path: Path, ocr_lang: str | None) -> dict` (worker output shape). `pdf_router.last_heading(doc: DoclingDocument) -> str | None` — text of the last `SECTION_HEADER` or `TITLE` item.

- [ ] **Step 1: Write the failing fast tests**

Fast tests stub `extract_worker.chunk_document` (monkeypatch it with a fake returning one chunk per doc: `{"text": "\n".join(texts), "heading_path": last SECTION_HEADER or None, "page": page, "token_count": 1}`) so no tokenizer download is needed, and stub `extract_worker._converter` where the docling route runs.

```python
def test_text_only_deck_never_calls_the_converter(tmp_path, monkeypatch):
    # 3 landscape text pages; _converter replaced by a function that raises AssertionError
    result = pdf_router.extract(pdf, None)
    assert result["pages"] == 3 and [c["page"] for c in result["chunks"]] == [1, 2, 3]

def test_untitled_page_inherits_previous_title(tmp_path, monkeypatch):
    # page 1 titled "Deadlock", page 2 untitled body → page-2 chunk heading_path == "Deadlock"

def test_first_untitled_page_has_no_heading(tmp_path, monkeypatch):          # Review Focus 4
    assert result["chunks"][0]["heading_path"] is None

def test_mixed_and_image_pages_go_to_docling_one_page_at_a_time(tmp_path, monkeypatch):
    # pages: text, image (cover 0.5), mixed (cover 0.10), text; stub converter records page_range
    assert calls == [(2, 2), (3, 3)]
    assert [c["page"] for c in result["chunks"]] == [1, 2, 3, 4]

def test_docling_page_title_carries_to_next_text_page(tmp_path, monkeypatch):
    # stub converter's doc for page 2 has a section header "Scheduling"; page 3 untitled text
    assert result["chunks"][-1]["heading_path"] == "Scheduling"

def test_image_page_with_no_text_is_skipped(tmp_path, monkeypatch):          # Review Focus 3
    # stub converter returns an empty DoclingDocument; text pages still chunk, no SystemExit

def test_model_load_failure_on_docling_page_is_retryable(tmp_path, monkeypatch):  # Review Focus 2
    # stub initialize_pipeline raises OSError → SystemExit with EXIT_MODEL_UNAVAILABLE

def test_unopenable_pdf_falls_back_to_docling_whole(tmp_path, monkeypatch):   # Review Focus 1
    # write b"%PDF-1.4 not really"; monkeypatch extract_worker._extract_docling to record + return a sentinel
    assert pdf_router.extract(bad, "ro") is sentinel and recorded == [(bad, "ro")]

def test_portrait_pdf_goes_to_docling_whole(tmp_path, monkeypatch):
    # 2 portrait pages → _extract_docling called once with the path

def test_handle_routes_pdf_to_the_router(tmp_path, monkeypatch):
    # monkeypatch pdf_router.extract to return a one-chunk dict; extract_worker._handle(pdf, out, None)
    # writes that dict to out
```

Stub converter: an object with `initialize_pipeline(fmt)` and `convert(path, page_range)` returning `SimpleNamespace(document=doc)`; install with `monkeypatch.setattr(extract_worker, "_converter", lambda lang: stub)`. Keep the autouse `_converter.cache_clear()` pattern from `test_extract_worker.py` out of this file — nothing here builds a real converter.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/ingestion/test_pdf_router.py -v`
Expected: the new tests FAIL (`AttributeError: extract`), Tasks 1–2 tests still PASS.

- [ ] **Step 3: Move the chunk loop into `chunk_document`**

Cut the loop and salvage from `_extract_docling` into `chunk_document(doc, page=None)`; `_extract_docling` becomes convert → `chunk_document(doc)` → `{"pages": …, "chunks": …}`. Behaviour of `_extract_docling` must not change.

Run: `uv run pytest tests/ingestion -v`
Expected: PASS (existing tests untouched and green).

- [ ] **Step 4: Implement `pdf_router.extract` and `last_heading`**

Order of decisions, each from the spec:
1. `pdfium.PdfDocument(path)`; on `pdfium.PdfiumError` return `extract_worker._extract_docling(path, ocr_lang)`.
2. Not `is_slide_deck(pdf)` → close, return `_extract_docling(path, ocr_lang)`.
3. Per page in order, tracking `carried: str | None`:
   - text/blank: `lines = page_lines(page)`; no lines → no chunks, `carried` unchanged; else `title, body = split_title(lines)`; `doc = text_page_document(n, w, h, title, body, carried if title is None else None)`; chunks `+= chunk_document(doc, page=n)`; `carried = title or carried`. Salvage never sees a carried heading: a page with its own lines but no title has body lines, so the chunker is not empty.
   - mixed/image: on the first such page of the file, `_model_step(lambda: _converter(ocr_lang).initialize_pipeline(InputFormat.PDF))`; `doc = _converter(ocr_lang).convert(path, page_range=(n, n)).document`; chunks `+= chunk_document(doc, page=n)`; `carried = last_heading(doc) or carried`.
4. Close the `PdfDocument` in a `finally` (the worker is warm across files). Return `{"pages": len(pdf), "chunks": chunks}`. `EXIT_NO_TEXT` stays in `_handle`.

Import `extract_worker` inside `extract` (or at module top — `pdf_router` is itself imported only from `_handle`), never from the parent side.

- [ ] **Step 5: Wire `_handle`**

`.pdf` suffix → `from groundly.ingestion import pdf_router; result = pdf_router.extract(in_path, ocr_lang)` before the `DOCLING_SUFFIXES` branch. Update the module docstring's first paragraph to say PDFs go through the page router.

- [ ] **Step 6: Run to verify the fast tests pass**

Run: `uv run pytest tests/ingestion -v`
Expected: PASS.

- [ ] **Step 7: Write the slow end-to-end test** in `tests/ingestion/test_extract_worker.py`

```python
@pytest.mark.slow
def test_landscape_deck_routes_pages_and_cites_each(tmp_path):
    # make_pdf: p1 text "Deadlock conditions"(32) + body(16); p2 blank title-only "Scheduling"(32)
    #   (one size on the page, so the modal rule reads it as body: a chunk "Scheduling", no heading);
    # p3 image page: a white 960x540 bitmap with "Circular wait occurs" drawn large (cover 1.0, no text layer);
    # p4 mixed: body text(16) + the same kind of bitmap at cover 0.10
    result = extract_worker._handle(...)  # or pdf_router.extract with the real converter/tokenizer
    pages = {c["page"] for c in result["chunks"]}
    assert pages == {1, 2, 3, 4}
    assert any("Circular wait" in c["text"] and c["page"] == 3 for c in result["chunks"])
    assert any(c["text"].startswith("Deadlock conditions") and c["page"] == 1 for c in result["chunks"])

@pytest.mark.slow
def test_blank_landscape_deck_exits_no_text(tmp_path, monkeypatch):
    # make_pdf with 2 empty landscape pages; main() → SystemExit(EXIT_NO_TEXT)
```

For the bitmap pages, render text with PIL (`ImageDraw`, `ImageFont.load_default(size=72)`) as `_image_only_pdf` does, and pass it through a `Page` field — extend `Page` with `image: PIL.Image | None = None` (used instead of the red square when set).

- [ ] **Step 8: Run the slow tests**

Run: `uv run pytest tests/ingestion/test_extract_worker.py -m slow -k "landscape" -v`
Expected: PASS (models load from the local cache).

- [ ] **Step 9: Full check**

Run: `uv run pytest && uv run ruff check && uv run pyrefly check && uv run lint-imports`
Expected: all pass. Then `uv run pytest -m slow` — all pass (existing slow tests prove the portrait/scanned/image paths are unchanged).

- [ ] **Step 10: Commit**

```bash
git add groundly/ingestion/extract_worker.py groundly/ingestion/pdf_router.py tests/ingestion/
git commit -m "feat(ingestion): route slide-deck PDF pages between pypdfium2 and docling"
```

---

### Task 4: Manifest label and decision 49 in the docs

**Files:**
- Modify: `groundly/core/manifest.py:64`
- Modify: `docs/groundly-spec.md` (§4 Extraction row line 93, §7 new decision 49), `docs/tech-stack/tech-stack.md` (line 11 extraction row, line 40 pins), `docs/architecture/data-model.md` (line 25 heading-path source, line 52 strategy), `docs/architecture/retrieval.md` (line 36 chunking paragraph), `docs/architecture/overview.md` (line 17), `docs/use-cases/knowledge-base.md` (line 15)
- Test: existing manifest/export tests

**Interfaces:**
- Consumes: the merged behaviour of Task 3.

- [ ] **Step 1: Change the label**

`strategy: str = "page-routed-hybrid"` in `groundly/core/manifest.py`. Run `uv run pytest tests -k manifest -v`; fix any test that asserts the old literal by updating the literal (no logic reads it).

- [ ] **Step 2: Record decision 49 with `/decision`**

Invoke the `decision` skill. Content, from the spec: slide-deck PDFs route per page (text/blank → pypdfium2 + slide-title rule + one-page `DoclingDocument` → HybridChunker; mixed/image → docling on that page alone); portrait PDFs and unopenable PDFs stay whole-file docling; `IMG_COVER = 0.05` = docling's `bitmap_area_threshold` (Paul, 2026-11-20), 0.3 rejected with its 543-page loss; measured numbers from the spec's table (apd 240 s → 178 s in the spike; hit@8 ±1 0.98 both; MRR 0.803 → 0.743); costs: heuristic headings, no TableFormer on text pages, small top-1 cost on split code slides, slide footers enter chunk text where docling marked them furniture; pypdfium2 pinned 5.14.0; strategy label `page-routed-hybrid`, no format bump, existing subjects change only on re-index. Link the spec. Update each doc listed above in the same change set; the `heading path from Docling HybridChunker` cell becomes "from the slide-title rule on text-layer slide pages, docling's layout model elsewhere".

- [ ] **Step 3: Verify docs and code agree**

Run: `grep -rn "docling-hybrid" docs groundly tests`
Expected: only the spec's "old bundles keep" line and decision 49's text.

- [ ] **Step 4: Commit**

```bash
git add groundly/core/manifest.py docs/
git commit -m "docs: decision 49 — PDF page router"
```

---

### Task 5: Recorded measurement (acceptance 4)

Not a CI test; the numbers go into decision 49 and `groundly-rs/bench/extract-alt/route/RESULTS.md`. Run on the M1 Pro, nothing else heavy running.

**Files:**
- Modify: `docs/groundly-spec.md` (decision 49's measured line), `groundly-rs/bench/extract-alt/route/RESULTS.md` (new section 6, outside this repo)

- [ ] **Step 1: Fresh index of apd with the built router**

```bash
export H=/private/tmp/router-apd && rm -rf $H && mkdir -p $H && ln -s ~/.groundly/models $H/models
GROUNDLY_HOME=$H /usr/bin/time -l uv run groundly index apd ~/.groundly/apd/materials
```

Expected: every file `indexed` (same failures as today's index, if any), wall time ≤ 190 s.

- [ ] **Step 2: Retrieval gate**

```bash
cd ~/Developer/groundly-rs/bench/extract-alt/route
~/Developer/groundly/.venv/bin/python eval_retrieval.py homes/docling prof $H apd out/eval-built.json
```

Expected: `B labeled tol=1` hit@8 ≥ 0.96. If it misses, rerun the spike's Markdown route (`IMG_COVER=0.05 SUFFIX=-md build_router.py apd homes/router05 out/r05.json`, then `eval_retrieval.py` against it) before changing anything, to see whether the direct document is the cause.

- [ ] **Step 3: Record and commit**

Write the wall time, chunk count, hit@8/MRR (exact and ±1) into decision 49 and RESULTS.md §6.

```bash
git add docs/groundly-spec.md
git commit -m "docs: decision 49 measured on the built router"
```
