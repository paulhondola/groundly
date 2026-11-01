# Security & Privacy Model

Single-user, local-first: most of the archived iteration's threat model (multi-tenant isolation, sandbox escape reaching other students' data, upload abuse) dissolved with the server. What remains is small, specific, and real. Ordered by exposure.

## 1. Import — the trust boundary

A `.groundly` bundle is third-party content that will enter the student's prompts and filesystem.

**Controls:**
- **Zip-slip protection**: extraction rejects entries escaping the target directory (no absolute paths, no `..`); symlinks not extracted.
- **Manifest validation before anything is read**: format version supported, counts sane; unknown schema versions refused (`PRAGMA user_version` check on the imported store.db).
- **Imported chunks are layer-3 data** — delimited, quoted, never instructions ([`../architecture/agents.md`](../architecture/agents.md)).
- Imported SQLite files are opened with the same schema checks as native ones; no code paths execute content from the bundle.
- **The topic tree is checked after extraction**: a bundle without the tree tables, with dangling references (`PRAGMA foreign_key_check`) or with a tree deeper than two levels is refused. `PRAGMA foreign_key_check` only covers keys a file declares, so explicit anti-joins also check `topic_citations.topic_id` and `chunk_id` and `topics.parent_id`. **The tree's cost is bounded**: more than 1000 topics, more than 5 citations on one topic, a title over 200 or a summary over 500 characters (or either not stored as text), or more than 64 MiB of cited chunk text refuses the bundle, so `tree()` cannot be made to allocate without limit. An SQLite error while reading a hostile schema is reported with its cause and the temporary import directory is removed. Imported titles and summaries are layer-3 text, shown by the page only as text (section 4).
- **Legacy `graph/` entries are never extracted** (decision 37). Bundles built before the graph was dropped still carry parquet; the entries pass the zip-slip and allowlist gates and are then skipped, so no parquet or lancedb reader exists in Groundly at all.

## 2. Prompt injection via documents

The student's *own* lecture PDFs are as capable of carrying "ignore previous instructions" as an import. All retrieved content — chunks, and the titles and summaries stored in a topic tree — is layer 3: instructions inside it are inert by construction of the immutable system layer.

## 3. Subprocess execution (verifier + coding challenges)

An idea, not planned (spec §3, coding challenges): the verifier would execute LLM-generated reference solutions for code questions. No subprocess runs generated code today; until it does, code questions are refused at the door. This is the student's own machine running code produced by the student's own chosen model — self-risk, but bounded anyway:

**Controls:** temp working directory, wall-clock timeout, output size cap, no shell interpolation of generated strings (argv exec). No network isolation is claimed — documenting that honestly beats pretending a sandbox exists. (The archived gVisor design existed because *our server* ran *other people's* code; that premise is gone.)

## 4. Local servers

`groundly mcp --http` (MCP over Streamable HTTP) and `groundly serve` (the topic-tree page) bind **127.0.0.1 only** — no-auth is acceptable exactly and only on loopback. Neither takes a `--host`. stdio MCP has no network surface at all.

**The topic-tree page** (`groundly serve`) is a new local surface, and a hostile page in the student's browser can reach loopback. What it exposes: read-only routes over every subject's tree, chunk text and stored PDFs. Controls:
- **Host header check** (`TrustedHost`: `127.0.0.1`, `localhost`) against DNS rebinding, as `mcp --http` has. The port is limited to 1–65535, and a busy or refused port is named by cause.
- **Read-only**: every route refuses POST/PUT/PATCH/DELETE (405), the page opens `store.db` only to read, and a test holds a connection and checks `PRAGMA data_version` is unchanged. Opening a store through `connect()` sets WAL mode, which changes no rows. It loads no model.
- **Untrusted text**: titles, summaries, filenames and chunk text may come from an imported bundle. The page inserts them with `textContent` only (a test bans the HTML-inserting sinks), under a per-response CSP nonce on HTML responses (`default-src 'none'`, `script-src` nonce only, `frame-ancestors 'none'`), with no external resource.
- **Files**: a PDF is addressed by material id, never by a path from the request; its stored name must resolve inside the subject's `materials/` directory (symlinks out, `..`, embedded NULs and non-PDF suffixes are refused), and it is served as `application/pdf` with `nosniff`. Other formats are never served raw — an HTML file from a bundle would otherwise run script on the page's origin — and show the cited chunk text instead.
- Residual: a PDF opens in the browser's own viewer on the page's origin, so a hostile PDF is a browser-viewer risk, not a Groundly one; the student's own materials carry the same risk anywhere.
- **Skill install**: `groundly skill install` and `uninstall` refuse a `SKILL.md` that is a symlink (dangling or not), with or without `--yes`, and name where it points; they never write through or unlink a link. A symlinked parent directory is allowed.

## 5. Privacy

- **Nothing leaves the machine** except model downloads from Hugging Face, plus RapidOCR models from modelscope.cn (sha256-pinned) only if a configured `--ocr-lang` resolves to a model not bundled in the rapidocr wheel.
- **The privacy boundary is a file**: `progress.db` — empty today, study progress in sub-project 3 — is never exported. The topic tree lives in `store.db`, so it exports with the bundle; the page and the tools write nothing to `progress.db`. `store.db` exports carry the whole knowledge base including chunk text and original materials; the export UX says so plainly ("this bundle contains everything indexed in this subject, including its topic map").
- **Sharing = sharing course-material text.** Between enrolled students this is note-sharing; Groundly documents it rather than policing it (thesis acknowledges the copyright surface).
- **Debug logs are stderr-only, never a Groundly-written file.** `--debug` (on `index`/`search`/`mcp`) and `GROUNDLY_LOG_LEVEL` stream to stderr and nowhere else: a log file would be a new artifact holding query text and chunk ids that export code would then have to reason about, so there isn't one. Off by default at *every* level — `groundly/__init__.py` attaches a `NullHandler` so stdlib's `logging.lastResort` can't emit WARNING+ records on its own. inside the host-spawned `groundly mcp` process the switch is either `--debug` in the host's `args` or `GROUNDLY_LOG_LEVEL` in its `env`; every record goes to stderr precisely because that process speaks the MCP protocol over stdout.
- No telemetry, no accounts, no third-party trace storage (LangSmith was dropped for exactly this reason).

## Residual risks, named

- A hostile store's table definitions are not compared with the shipped schema: the checks above bound what the topic tables hold, not how an imported file declares them.
- Imported text (a title, summary or chunk) may try to steer a host into calling `submit_tree`. The `outline` and `submit_tree` descriptions and the skill tell the host to treat course text as data, but nothing can prevent a host from obeying it; the gate still bounds what such a call can store, and it writes only the topic tree.
- A malicious `.groundly` bundle with a crafted SQLite file targeting parser bugs — mitigated by schema checks, not eliminated.
- A malicious `.groundly` bundle sized as a zip bomb — declared uncompressed sizes are checked before anything is decompressed (manifest.json capped at 1 MB, bundle total at 20 GiB), relying on zipfile's own enforcement of each entry's declared size on read; a legitimately-huge bundle under that cap exceeding the student's free disk still fails, but at extraction with an OS error, not silently.
- Docling parsing a hostile PDF (from a merge-by-reindex of imported materials) — contained to the extraction subprocess (`extraction_failed`), not the app.
- OCR rasterization on adversarial page geometries (a huge MediaBox rasterizes to multi-GB bitmaps) — bounded only by the extraction subprocess's wall-clock timeout, no memory cap; an OOM kill is contained to the child (`extraction_failed`) but the machine takes the memory pressure first. Standalone image inputs (decision 17) share this exact risk class — a huge-dimension or multi-frame raster decodes in the same subprocess, same timeout bound, no explicit pixel/frame cap. These bounds are now **user-tunable** (decision 18): `ingestion.timeout_seconds` and `ingestion.max_image_pixels` default to the shipped values but a user may raise (weaken) or lower (harden) them on their own machine, and an **opt-in** `ingestion.max_file_size_mb` rejects oversize inputs before the subprocess spawns. The image-pixel cap is passed to the worker as an integer env var (`GROUNDLY_MAX_IMAGE_PIXELS`), not through the hostile-document channel.
- Generated code doing something hostile inside the timeout — accepted as self-risk on the student's own machine, stated in docs.
