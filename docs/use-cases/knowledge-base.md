# Use Cases: Knowledge Base (UC-01 – UC-03)

Detail for [`groundly-spec.md`](../groundly-spec.md) §3. Actor: the **student** (CLI) or a **host agent** acting for them (MCP). "Done" = acceptance criteria pass.

---

## UC-01 — Index materials

**Actor:** student (CLI).
**Preconditions:** subject initialized (`groundly init <SUBJECT>`); files are PDF/DOCX/PPTX/MD/HTML/LaTeX/AsciiDoc/CSV/XLSX/EPUB/TXT/source or a standalone raster image (PNG/JPG/JPEG/TIF/TIFF/BMP/WEBP) (a text layer is no longer required — scanned PDFs and images are allowed, OCR'd on extraction; decision 17).

**Main flow**

1. `groundly index <SUBJECT> <paths...>` — files are hashed (sha-256); already-indexed hashes are skipped (idempotent re-run = the "new lecture this week" workflow; no watch daemon). `--ocr-lang <code>` (e.g. `ro`) sets the subject's OCR language on first use — recorded in the manifest; changing it later requires re-indexing and is refused with a specific message (decision 15).
2. Per file, in one transaction: Docling extraction **in a subprocess** (OCR via bundled RapidOCR for scanned/bitmap regions) → HybridChunker (section-aligned, heading path prepended) → bge-m3 dense + learned sparse (lazy-loaded, local) → sqlite-vec / sparse table / FTS5 rows → `indexed`.
3. Progress per file (`queued → extracting → embedding → indexed`), rich CLI output.
4. Corpus hash changed → offer the graph build with a **cost estimate first** (skippable; vector-only subjects are first-class — see UC-12).

**Alternate / error flows**

- **A1 — Scanned/image-only PDF or standalone image:** indexes via OCR (bundled RapidOCR); a standalone image (PNG/JPG/…) is treated as a one-page scanned PDF (page-1 attribution). A multi-frame raster (multi-page TIFF, animated WEBP) indexes its first frame only — multi-page scans belong in a PDF (decision 17). The remaining failure is no readable text even after OCR → `extraction_failed` with "no readable text — OCR found nothing to extract".
- **A2 — Parser crash on a hostile/broken file:** subprocess dies → that file is `extraction_failed`; the run continues.
- **A3 — Duplicate (same hash, same subject):** skipped, reported as duplicate.
- **A4 — Ctrl-C mid-run:** per-file transactions mean at most the in-flight file is lost; re-run resumes.

**Acceptance criteria**

- A real digital lecture PDF round-trips to retrievable chunks with **correct page attribution and heading paths**.
- A scanned PDF indexes via OCR with correct page attribution; a document with no readable text even after OCR fails cleanly with the specific message; every file reaches a terminal state.
- Re-running `index` on an unchanged folder does no re-embedding; adding one file embeds exactly one file.
- An interrupted run resumes without corruption (WAL) while a live MCP process keeps answering.

---

## UC-02 — Grounded Q&A

**Actor:** host agent (MCP `ask`/`search`) or student (`groundly ask`).
**Preconditions:** subject has ≥1 indexed material; `ask` additionally needs a configured chat provider.

**Main flow (`ask` — the enforced path)**

1. Router classifies the query (factoid / multi-hop / global) — also the cost gate for graph paths.
2. Retrieval arm(s) fire (three-channel vector baseline; graph if routed and built); RRF fusion; cross-encoder rerank (default ON).
3. Prompt assembled in trust layers; retrieved content is delimited data.
4. Generation → **citation resolution**: every claim carries chunk ids resolving to document + page + heading path. Zero resolvable citations = error.
5. Response: cited answer, or **"not covered by the course materials"** — never model knowledge.
6. Trace row recorded (arm, path, chunk ids, tokens, cost, latency) in `progress.db`.

**Main flow (`search` — the raw path)**: query → same retrieval stack → top-k chunks with text + citations returned to the host, which composes its own answer (best-effort grounding, measured — not enforced).

**Acceptance criteria**

- Every `ask` answer contains ≥1 citation resolving to the correct page; the no-coverage case returns the refusal, not a hallucination.
- A Romanian question over English-only slides retrieves relevant chunks (dense channel; cross-lingual slice in the eval).
- `groundly ask` and the MCP `ask` tool produce identical results for the same query (same function).
- With no API key configured, `ask` fails with a clear message while `search` works fully.

---

## UC-03 — Source management

**Actor:** student (CLI) or host agent (`list_subjects`, read-only).

**Main flow**

1. `groundly list <SUBJECT>` shows materials with status, page counts, chunk counts; `list_subjects` (MCP) exposes the same.
2. Removing a material deletes its chunks/vectors/sparse/FTS rows immediately; the graph rebuilds on the next corpus-hash-triggered run (lag surfaced in output).
3. `manifest.json` counts stay in sync after every mutation.

**Acceptance criteria**

- Deleting a material leaves no retrievable chunks in any channel.
- `list_subjects` works from an MCP host spawned in an arbitrary working directory (global `~/.groundly/` discovery).
