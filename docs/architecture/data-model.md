# Data Model & Interchange Format

The storage backbone for [`overview.md`](overview.md). SQLite (WAL) per subject; schema versioned via `PRAGMA user_version` — no migration framework. Column detail belongs in code, not docs.

## Layout

```
~/.groundly/                 # global (GROUNDLY_HOME overrides) — MCP hosts spawn
  config.toml                # with arbitrary cwd, so discovery must not be per-project
  .host-run/                 # a page run's empty working directory (decision 46); never a subject
  <SUBJECT>/
    manifest.json            # the interchange contract (below)
    materials/               # original files — citation targets
    store.db                 # the knowledge base            → EXPORTED
    progress.db              # the student's private state   → NEVER EXPORTED
  activity.db                # what hosts and index runs are doing → NEVER EXPORTED
  start.json                 # a running `groundly start`'s pid, port, version (decision 54) → NEVER EXPORTED
```

`list_subjects` = a scan of `~/.groundly/*/manifest.json`. No registry database.

## store.db (exported)

| Table | Contents | Notes |
|---|---|---|
| materials | filename, sha-256, status (`indexed`/`extraction_failed`), page count | unique hash per subject = duplicate rejection |
| chunks | material FK, page ref, **heading path**, text, token count | the citation unit; ids are never reused, so a stored citation can't come to mean a different chunk; heading path from the slide-title rule on text-layer slide pages, docling's layout model elsewhere |
| vectors | sqlite-vec virtual table, bge-m3 dense (1024-d) | exact KNN (brute force — an upgrade over approximate HNSW) |
| sparse_terms | inverted index of bge-m3 learned sparse weights | same forward pass as dense |
| chunks_fts | FTS5 index over chunk text | BM25 channel |
| topics | parent FK (null = big topic), position, title, summary | the topic tree, exactly two levels; ids are never reused, so progress can key on them later; a parent's deletion cascades to its subtopics |
| questions | topic FK `ON DELETE CASCADE`, kind (`single`/`multiple`/`open`), stem, code, reference answer | the question bank, written only through the question gate; ids are never reused, so a recorded answer cannot come to mean another question |
| question_options | question FK cascade, position, text, correct flag | choice kinds only |
| question_citations | question FK, chunk FK, both cascade | 1 to 5 per question; a trigger deletes a question when its last citation goes |
| topic_citations | topic FK, chunk FK, rank, both `ON DELETE CASCADE` | a node's sources, chosen by the topic gate; removing a material drops its citations and leaves the node, flagged in the page, with no sources |

The two tree tables sit in the base schema. The question tables arrived at `user_version = 2` (decision 44); `connect()` upgrades a version-1 store in place under the write lock, adding only the empty question tables, and refuses one that already holds an object named like them. Titles and summaries are host-written text, never a citation target; only `topic_citations` rows, which point at verbatim chunks, are citations.

**Schema version 2 (decision 44).** The question tables are the one in-place upgrade: opening a version-1 `store.db` creates them, empty, under the write lock, and touches no existing row. An imported version-1 bundle is upgraded the same way, and one that already holds an object of the same name is refused.

## progress.db (never exported)

Two tables at `PRAGMA user_version = 2` (decision 44): `attempts` (one per submitted test, unique by the page's submit token so a repeated Submit writes nothing) and `answers` (one per question shown, with the response, whether it was right, and for an open answer the host's later verdict and comment). Question and topic ids are plain integers, never foreign keys into `store.db`, so history survives a question or topic that is later removed. A version-1 file (created empty) gets its tables on first open. `search` and the topic tree write nothing here.

## activity.db (never exported)

One machine-wide table, `events` (`at`, `pid`, `source` in `mcp_stdio`/`mcp_http`/`index`, `session`, `client`, `subject`, `event`, a small JSON `detail`), at `PRAGMA user_version = 1`, mode `0600` (tightened back on write if loosened), created on first write (decision 53). The MCP middleware and `groundly index` append to it, best-effort; the page reads it for the host card, the orb, indexing progress and recent results. It holds no course text: valid subject names, Groundly's own tool names, counts, file names, an index run's failure message and the host's self-reported name (capped), or `Groundly page run` for a page run's requests. Rows older than 7 days are pruned when a session or index run starts. Export and import never read it.

## manifest.json — the interchange contract

```json
{
  "format_version": 1,
  "subject": "PDSS",
  "title": "Parallel and Distributed Systems",
  "embedding": { "model": "BAAI/bge-m3", "hf_revision": "<pin>", "dim": 1024,
                  "dtype": "float16", "normalized": true },
  "chunking":  { "strategy": "page-routed-hybrid", "max_tokens": 512, "overlap": 0 },
  "ocr":       { "engine": "rapidocr-onnxruntime", "lang": [] },
  "counts":    { "materials": 0, "chunks": 0 },
  "tool_version": "<groundly version>"
}
```

`title` is an optional display label (`""` when unset, set with `groundly title`): at most 120 visible characters on one line, checked whenever a manifest is read, so a bad one in a bundle refuses the import. Commands, paths and citations always use `subject`, so changing a title moves nothing (decision 42). Adding it is not a `format_version` event: an older groundly ignores it, and drops it if that version rewrites the manifest.

Semantics: vectors transfer **as-is only on exact embedding match** (model + revision + dim + dtype + normalization) — the global bge-m3 pin makes this the default. The engine is not part of the match: MLX and torch vectors agree within decision 48's tolerance and transfer as-is. Mismatch → re-embed from chunk text (which is why chunk text always ships). Bundles built before decision 37 carry a `graphrag` manifest block and a `graph/` directory; both are ignored on import (the directory is never extracted), and dropping them is not a `format_version` event. Bundles built before decision 38 carry a `store.db` with the dropped deck tables; import refuses them with a re-index message (no users existed, so there is no migration). `ocr.lang` records the subject's OCR language set via `groundly index --ocr-lang` (`[]` = bundled default model set); it is part of the interchange contract because it shapes extracted chunk text — a re-index with a different lang is a different corpus (decision 15).

## Export / import

- **Export** = zip an allowlist (`manifest.json`, `store.db`, `materials/`) — never `progress.db` → `PDSS.groundly`. Original files included by default (importer's citations must open the right page); `--no-materials` to shrink. The export UX states plainly: "this bundle contains everything indexed in this subject, including its topic map and question bank."
- **Import** = validate manifest → zip-slip-safe extraction → check the topic tree (tree tables present, `PRAGMA foreign_key_check` clean, no node deeper than two levels; otherwise refused) → check the question bank (the gate's structure checks and limits repeated, no question on a topic with subtopics; grounding is not repeated, since import loads no model) → fresh empty `progress.db`. Name collision → import-as-new-name or replace-with-confirm. **No merge in v1**; honest merge = import the materials and re-index the union.
- Imported chunks are **untrusted layer-3 content**.

## Integrity rules as constraints, not app code

- Unique `(subject) file sha-256` — duplicate rejection.
- Stored questions must carry 1 to 5 chunk id FKs that resolve — enforced at the question gate, by FK, and by the trigger that deletes a question with its last citation.
- One stem per topic (compared trimmed and case-folded, which SQLite's ASCII-only `lower()` cannot express) and at most 200 questions per topic: app code, checked by the question gate and again by `add_questions` under its `BEGIN IMMEDIATE` write lock, the only place a question is inserted.
- Topic sources are `topic_citations` FKs to chunks; the topic gate writes only ids it got back from its own search. `AUTOINCREMENT` on `chunks.id` and `topics.id` keeps a deleted id from being reused, so a stored citation can never come to mean a different chunk.
- `PRAGMA user_version` checked on open; refuse to write to a newer schema than the tool understands.
