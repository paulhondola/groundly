# Adversarial review: standalone-image-ingestion (2026-07-20)

Verdict: MERGE WITH FIXES

Branch `standalone-image-ingestion` (commit 5e133e2). Reviewed against decision 17,
UC-01, `.claude/rules/grounding-and-privacy.md`, `.claude/rules/architecture.md`.
docling==2.113.0, rapidocr==3.9.1 (as installed).

## Findings

### F1 — Multi-page TIFF/animated WEBP break the "one-page / page-1 attribution" contract and misattribute later-page text to page 1 [severity: medium]
- Where: `docs/groundly-spec.md` decision 17 + `docs/use-cases/knowledge-base.md` A1
  (claim); `groundly/ingestion/extract_worker.py:97-108` (chunker + page pick);
  `groundly/ingestion/formats.py:28-29` (`.tif/.tiff` admitted, `.webp` may be animated).
- Failure scenario: index a 2-frame `.tiff` (or animated `.webp`). docling's
  `ImageDocumentBackend` (`.venv/.../docling/backend/image_backend.py:160-176`) expands
  every frame to a page (`page_no = i+1`), so `pages == 2`, not 1. `HybridChunker(merge_peers=True)`
  then merges peer text across frames into a single chunk, and the page is taken from the
  first `doc_item`'s prov → `page == 1`. Result: text that is physically on frame 2 is
  stored and cited as page 1. A `groundly://<subject>/<file>#page=1` citation / `get_page`
  read resolves to frame 1, which does not contain the cited sentence — a grounding
  integrity violation (`grounding-and-privacy.md`: citations must resolve to document + page).
- Evidence: verified by reproduction. A 2-frame TIFF produced `pages: 2, chunks: 1`,
  `page=1`, and the single chunk's text contained both `"Alpha body sentence…"` (frame 1)
  and `"final readable line on page two here"` (frame 2). The docs assert "one-page scanned
  PDF (page-1 attribution)" in three places; that is false for every multi-frame input in
  the allowlist. No test covers a multi-frame image. (The cross-page merge_peers behavior
  is shared with multi-page PDFs, so the root is the chunker — but this feature is what
  newly admits multi-frame images under an explicit single-page claim.)
- Fix options: read only frame 0 for images, or drop the "one-page" wording and accept
  per-frame pages (they resolve correctly per-frame — only the cross-frame *merge* is
  wrong), and add a multi-frame test.

### F2 — "Without ImageFormatOption docling falls back to EasyOCR" is factually wrong for docling 2.113.0 [severity: low]
- Where: `groundly/ingestion/extract_worker.py:81-83` (comment) and `docs/groundly-spec.md`
  decision 17 ("**this registration is load-bearing** — without it docling's IMAGE default
  falls back to EasyOCR (runtime model downloads)").
- Failure scenario: the stated justification is untrue, so a future maintainer reasons from
  a false premise. docling's default IMAGE option uses `OcrAutoOptions`, resolved by
  `auto_ocr_model.OcrAutoModel` (`.venv/.../docling/models/stages/ocr/auto_ocr_model.py`):
  on `darwin` it picks **ocrmac** first; on `linux` it tries nemotron, then **rapidocr**
  (chosen whenever onnxruntime+rapidocr are present — which the pin guarantees); EasyOCR is
  reached only if rapidocr is absent. So EasyOCR is effectively unreachable in the shipped
  env, and the offline pin is not what's at risk. The real reason to register is engine
  *consistency*: on the dev machine (macOS) auto would silently use ocrmac — a different,
  offline engine — producing different OCR output than the pinned RapidOCR, which is the
  decision-14 interchange-compatibility pin. The registration is correct; the reason is not.
- Evidence: read the auto-resolution source (order: ocrmac→nemotron→rapidocr→easyocr);
  confirmed rapidocr 3.9.1 installed and its models load from the bundled wheel offline
  ("File exists and is valid" in repro logs, no download).

### F3 — Neither slow test actually guards the load-bearing registration [severity: low]
- Where: `tests/ingestion/test_extract_worker.py:174-192`
  (`test_standalone_image_indexes_via_ocr`), `:81-92` (`test_image_format_uses_pinned_rapidocr`).
- Failure scenario: delete the `InputFormat.IMAGE: ImageFormatOption(...)` line and the
  suite still passes. The happy-path test would OCR fine via auto→ocrmac on macOS (or
  auto→rapidocr on linux) and still assert non-empty chunks + page==1. The "pinned" test
  only inspects the `format_options` dict the code just constructed — it asserts the config
  equals itself, never that the unregistered path uses a different/failing engine. So the
  claim the tests exist to defend ("without it, EasyOCR") is unverified by the tests.
- Evidence: read both tests + the `_captured_format_options` stub (construction-only, raises
  before any model load).

### F4 — Title-only / heading-only image reports "OCR found nothing to extract" though OCR read the title [severity: low]
- Where: `groundly/ingestion/extract_worker.py:151-153` (any-text-strip → EXIT_NO_TEXT) →
  `groundly/ingestion/extract.py:88-91` (image message).
- Failure scenario: an image whose only text is a single heading line yields zero body
  chunks from HybridChunker (noted in the fixture comment itself), so it exits EXIT_NO_TEXT
  and the student is told "no readable text — OCR found nothing to extract" — but OCR did
  read the title. The message names the wrong cause (real cause: no body text to chunk).
  Pre-existing for title-only PDFs; the image path inherits it. Minor, but violates the
  conventions rule that failure messages name the specific cause.
- Evidence: read the code path; the `_image_with_text` fixture comment documents the
  heading-only→no-chunks behavior the author designed around.

### F5 — No image-dimension cap on attacker-supplied images (layer-4 data) [severity: low]
- Where: `groundly/ingestion/formats.py:24-32` admits images; decode happens in the worker
  via docling→Pillow (`image_backend.py` `Image.open(...).convert("RGB")`, all frames eager).
- Failure scenario: a hostile image between Pillow's `MAX_IMAGE_PIXELS` (~89M) and 2× that
  only warns and still fully decodes; multi-frame formats decode every frame to RGB
  (~3 bytes/px/frame) at once, with no `RLIMIT_AS`/memory cap on the subprocess. Bounded by
  the 300 s wall-clock timeout and process isolation (a crash is `parser failed`, run
  continues), and Pillow raises `DecompressionBombError` above 2×. Same exposure class as
  PDF page rendering, so not newly severe — flagging because accepting arbitrary raster
  images widens the decompression-bomb surface and there is no explicit pixel/byte guard.
- Evidence: read Pillow default behavior + the eager multi-frame loop in `image_backend.py`;
  no rlimit set in `extract.py`'s `subprocess.run` (argv/timeout/tempdir controls only).

## What I tried and could not break
- Uppercase extensions (`.PNG`): every gate lowercases (`pipeline.py`, `extract_worker.main`,
  `_extract_docling`, `extract.py:89`) — routes correctly.
- `IMAGE_SUFFIXES` import in `extract.py`: no cycle; `formats.py` is stdlib-only. Suite imports fine.
- Single-frame PNG: page==1 and blank PNG → EXIT_NO_TEXT → correct OCR message, both verified
  by the passing slow tests (`5 passed`).
- Offline/zero-key: rapidocr models load from the bundled wheel, no network fetch (repro logs).
- `.groundlyignore` / dotfile / symlink handling: unchanged by this diff; images ride existing gates.
