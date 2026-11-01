# Data Model & Interchange Format

The storage backbone for [`overview.md`](overview.md). SQLite (WAL) per subject; schema versioned via `PRAGMA user_version` — no migration framework. Column detail belongs in code, not docs.

## Layout

```
~/.groundly/                 # global (GROUNDLY_HOME overrides) — MCP hosts spawn
  config.toml                # with arbitrary cwd, so discovery must not be per-project
  <SUBJECT>/
    manifest.json            # the interchange contract (below)
    materials/               # original files — citation targets
    store.db                 # the knowledge base            → EXPORTED
    progress.db              # the student's private state   → NEVER EXPORTED
```

`list_subjects` = a scan of `~/.groundly/*/manifest.json`. No registry database.

## store.db (exported)

| Table | Contents | Notes |
|---|---|---|
| materials | filename, sha-256, status (`indexed`/`extraction_failed`), page count | unique hash per subject = duplicate rejection |
| chunks | material FK, page ref, **heading path**, text, token count | the citation unit; ids are never reused, so a stored citation can't come to mean a different chunk; heading path from Docling HybridChunker |
| vectors | sqlite-vec virtual table, bge-m3 dense (1024-d) | exact KNN (brute force — an upgrade over approximate HNSW) |
| sparse_terms | inverted index of bge-m3 learned sparse weights | same forward pass as dense |
| chunks_fts | FTS5 index over chunk text | BM25 channel |
| topics | parent FK (null = big topic), position, title, summary | the topic tree, exactly two levels; ids are never reused, so progress can key on them later; a parent's deletion cascades to its subtopics |
| topic_citations | topic FK, chunk FK, rank, both `ON DELETE CASCADE` | a node's sources, chosen by the topic gate; removing a material drops its citations and leaves the node, flagged in the page, with no sources |

The two tree tables sit in the single schema at `user_version = 1` with no migration: a subject indexed before they existed works for search and raises SQLite's own "no such table" on tree calls until it is dropped and re-indexed once. Titles and summaries are host-written text, never a citation target; only `topic_citations` rows, which point at verbatim chunks, are citations.

## progress.db (never exported)

No tables yet: the file is created empty (`PRAGMA user_version = 1`) so the privacy boundary exists before anything lives behind it. Quizzes (sub-project 3) put study progress here, keyed by topic id and never joined to `store.db` by foreign key, so a topic that returns under the same id keeps its history. The topic tree (sub-project 2) writes nothing here: every node reads "Not started". `search` writes nothing: every query the student asks used to land in this file, and nothing read those rows.

## manifest.json — the interchange contract

```json
{
  "format_version": 1,
  "subject": "PDSS",
  "embedding": { "model": "BAAI/bge-m3", "hf_revision": "<pin>", "dim": 1024,
                  "dtype": "float16", "normalized": true },
  "chunking":  { "strategy": "docling-hybrid", "max_tokens": 512, "overlap": 0 },
  "ocr":       { "engine": "rapidocr-onnxruntime", "lang": [] },
  "counts":    { "materials": 0, "chunks": 0 },
  "tool_version": "<groundly version>"
}
```

Semantics: vectors transfer **as-is only on exact embedding match** (model + revision + dim + dtype + normalization) — the global bge-m3 pin makes this the default. Mismatch → re-embed from chunk text (which is why chunk text always ships). Bundles built before decision 37 carry a `graphrag` manifest block and a `graph/` directory; both are ignored on import (the directory is never extracted), and dropping them is not a `format_version` event. Bundles built before decision 38 carry a `store.db` with the dropped deck tables; import refuses them with a re-index message (no users existed, so there is no migration). `ocr.lang` records the subject's OCR language set via `groundly index --ocr-lang` (`[]` = bundled default model set); it is part of the interchange contract because it shapes extracted chunk text — a re-index with a different lang is a different corpus (decision 15).

## Export / import

- **Export** = zip an allowlist (`manifest.json`, `store.db`, `materials/`) — never `progress.db` → `PDSS.groundly`. Original files included by default (importer's citations must open the right page); `--no-materials` to shrink. The export UX states plainly: "this bundle contains everything indexed in this subject, including its topic map."
- **Import** = validate manifest → zip-slip-safe extraction → check the topic tree (tree tables present, `PRAGMA foreign_key_check` clean, no node deeper than two levels; otherwise refused) → fresh empty `progress.db`. Name collision → import-as-new-name or replace-with-confirm. **No merge in v1**; honest merge = import the materials and re-index the union.
- Imported chunks are **untrusted layer-3 content**.

## Integrity rules as constraints, not app code

- Unique `(subject) file sha-256` — duplicate rejection.
- Stored questions (sub-project 3) must carry ≥1 chunk id FK that resolves — enforced at the verifier gate *and* by FK.
- Topic sources are `topic_citations` FKs to chunks; the topic gate writes only ids it got back from its own search. `AUTOINCREMENT` on `chunks.id` and `topics.id` keeps a deleted id from being reused, so a stored citation can never come to mean a different chunk.
- `PRAGMA user_version` checked on open; refuse to write to a newer schema than the tool understands.
