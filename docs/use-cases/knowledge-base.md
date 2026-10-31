# Use Cases: Knowledge Base (UC-01 – UC-03)

Detail for [`groundly-spec.md`](../groundly-spec.md) §3. Actor: the **student** (CLI) or a **host agent** acting for them (MCP). "Done" = acceptance criteria pass.

---

## UC-01 — Index materials

**Actor:** student (CLI).
**Preconditions:** none for the subject — `groundly index <SUBJECT> <paths>` creates it on first run, after checking every path exists; files are PDF/DOCX/PPTX/MD/HTML/LaTeX/AsciiDoc/CSV/XLSX/EPUB/TXT/source or a standalone raster image (PNG/JPG/JPEG/TIF/TIFF/BMP/WEBP) (a text layer is no longer required — scanned PDFs and images are allowed, OCR'd on extraction; decision 17).

**Main flow**

1. `groundly index <SUBJECT> <paths...>` — files are hashed (sha-256); already-indexed hashes are skipped (idempotent re-run = the "new lecture this week" workflow; no watch daemon). `--ocr-lang <code>` (e.g. `ro`) sets the subject's OCR language on first use — recorded in the manifest; changing it later requires re-indexing and is refused with a specific message (decision 15).
2. Per file, in one transaction: Docling extraction **in a subprocess** (OCR via bundled RapidOCR for scanned/bitmap regions) → HybridChunker (section-aligned, heading path prepended) → bge-m3 dense + learned sparse (lazy-loaded, local) → sqlite-vec / sparse table / FTS5 rows → `indexed`.
3. Progress per file (`queued → extracting → embedding → indexed`), rich CLI output.

**Alternate / error flows**

- **A1 — Scanned/image-only PDF or standalone image:** indexes via OCR (bundled RapidOCR); a standalone image (PNG/JPG/…) is treated as a one-page scanned PDF (page-1 attribution). A multi-frame raster (multi-page TIFF, animated WEBP) indexes its first frame only — multi-page scans belong in a PDF (decision 17). The remaining failure is no readable text even after OCR → `extraction_failed` with "no readable text — OCR found nothing to extract".
- **A2 — Parser crash on a hostile/broken file:** subprocess dies → that file is `extraction_failed`; the run continues.
- **A3 — Duplicate (same hash, same subject):** skipped, reported as duplicate.
- **A4 — Ctrl-C mid-run:** per-file transactions mean at most the in-flight file is lost; re-run resumes.
- **A5 — Retrying a failure:** later runs skip an `extraction_failed` file (a slow OCR timeout must not repeat every run) and say so; `groundly index --retry-failed` re-extracts it, e.g. after raising `ingestion.timeout_seconds`.

**Acceptance criteria**

- A real digital lecture PDF round-trips to retrievable chunks with **correct page attribution and heading paths**.
- A scanned PDF indexes via OCR with correct page attribution; a document with no readable text even after OCR fails cleanly with the specific message; every file reaches a terminal state.
- Re-running `index` on an unchanged folder does no re-embedding; adding one file embeds exactly one file.
- An interrupted run resumes without corruption (WAL) while a live MCP process keeps answering.

---

## UC-02 — Grounded search

**Actor:** host agent (MCP `search`) or student (`groundly search`).
**Preconditions:** subject has ≥1 indexed material. No provider needed, ever.

**Main flow**

1. `search(subject, query, k=None)` → three-channel retrieval (bge-m3 dense + learned sparse + FTS5/BM25), RRF fusion, cross-encoder rerank (default ON), truncated to `retrieval.top_k`.
2. Each hit returns verbatim text plus `chunk_id`, `filename`, `page`, `heading_path` and a `groundly://` URI.
3. The host composes the answer and cites the chunks it used. Grounding here is **best-effort by construction** — the server's `instructions` and the tool description carry the norm (decision 31); the enforced-pipeline alternative was measured and removed (decision 33).
4. `get_page(uri)` takes a hit's `uri` verbatim and opens exactly what it points to — that page, or the whole material when the URI has no `#page` (plain-text materials). Read as an MCP resource, the same URI returns the whole document grouped by page.

**Acceptance criteria**

- Every hit resolves to the correct page of the correct material.
- A Romanian question over English-only slides retrieves relevant chunks (dense channel).
- `groundly search` and the MCP `search` tool return the same hits for the same query.
- With no `config.toml` at all, both work.

---

## UC-03 — Source management

**Actor:** student (CLI) or host agent (`list_subjects`, read-only).

**Main flow**

1. `groundly list <SUBJECT>` shows materials with status, page counts, chunk counts; `list_subjects` (MCP) exposes the same.
2. Removing a material deletes its chunks/vectors/sparse/FTS rows immediately.
3. `manifest.json` counts stay in sync after every mutation.

**Acceptance criteria**

- Deleting a material leaves no retrievable chunks in any channel.
- `list_subjects` works from an MCP host spawned in an arbitrary working directory (global `~/.groundly/` discovery).
