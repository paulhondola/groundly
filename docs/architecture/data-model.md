# Data Model & Interchange Format

The storage backbone for [`overview.md`](overview.md). SQLite (WAL) per subject; schema versioned via `PRAGMA user_version` — no migration framework. Column detail belongs in code, not docs.

## Layout

```
~/.groundly/                 # global (GROUNDLY_HOME overrides) — MCP hosts spawn
  config.toml                # with arbitrary cwd, so discovery must not be per-project
  <SUBJECT>/
    manifest.json            # the interchange contract (below)
    materials/               # original digital files — citation targets
    store.db                 # the knowledge base            → EXPORTED
    progress.db              # the student's private state   → NEVER EXPORTED
    graph/                   # MS graphrag parquet artifacts → exported
```

`list_subjects` = a scan of `~/.groundly/*/manifest.json`. No registry database.

## store.db (exported)

| Table | Contents | Notes |
|---|---|---|
| materials | filename, sha-256, status (`indexed`/`extraction_failed`), page count | unique hash per subject = duplicate rejection |
| chunks | material FK, page ref, **heading path**, text, token count | the citation unit; heading path from Docling HybridChunker |
| vectors | sqlite-vec virtual table, bge-m3 dense (1024-d) | exact KNN (brute force — an upgrade over approximate HNSW) |
| sparse_terms | inverted index of bge-m3 learned sparse weights | same forward pass as dense |
| chunks_fts | FTS5 index over chunk text | BM25 channel |
| questions / decks | verified items only: body, answer key, distractors, cited chunk ids, verify status, generation source (`server`/`host`) | generation source feeds the rejection-rate experiment |
| subject_profile | markdown, size-capped | trust layer 2; shippable |

## progress.db (never exported)

| Table | Contents |
|---|---|
| quiz_events | question FK, correctness, timestamp — feeds mastery |
| notes | host-written `remember()` notes (layer-4 data on recall) |
| traces | per query: arm, router label, retrieved chunk ids, tokens, latency, cost |

Traces contain every question the student ever asked — which is exactly why they live here and not in the exported file. Mastery per graph community = `quiz_events` joined to the graph's Leiden communities; recomputable, not stored.

## manifest.json — the interchange contract

```json
{
  "format_version": 1,
  "subject": "PDSS",
  "embedding": { "model": "BAAI/bge-m3", "hf_revision": "<pin>", "dim": 1024,
                  "dtype": "float32", "normalized": true },
  "graphrag":  { "version": "<exact pin>", "extraction_model": "<model used>" },
  "chunking":  { "strategy": "docling-hybrid", "max_tokens": 512, "overlap": 0 },
  "counts":    { "materials": 0, "chunks": 0 },
  "tool_version": "<groundly version>"
}
```

Semantics: vectors transfer **as-is only on exact embedding match** (model + revision + dim + normalization) — the global bge-m3 pin makes this the default. Mismatch → re-embed from chunk text (which is why chunk text always ships). The graph is text-only parquet — model-independent, always portable — but `extraction_model` is recorded because an imported graph built by a different model is a different experimental condition.

## Export / import

- **Export** = zip the subject dir **minus `progress.db`** → `PDSS.groundly`. Original files included by default (importer's citations must open the right page); `--no-materials` to shrink. The export UX states plainly: "this bundle contains everything indexed in this subject."
- **Import** = validate manifest → zip-slip-safe extraction → fresh empty `progress.db`. Name collision → import-as-new-name or replace-with-confirm. **No merge in v1**; honest merge = import the materials and re-index the union.
- Imported chunks, summaries, and profiles are **untrusted layer-4 content** (subject profiles additionally size-capped, no authority).

## Integrity rules as constraints, not app code

- Unique `(subject) file sha-256` — duplicate rejection.
- Questions/cards must carry ≥1 chunk id FK that resolves — enforced at the verifier gate *and* by FK.
- `PRAGMA user_version` checked on open; refuse to write to a newer schema than the tool understands.
