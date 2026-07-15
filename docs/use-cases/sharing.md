# Use Case: Sharing Knowledge Bases (UC-30)

Detail for [`unilearn-spec.md`](../unilearn-spec.md) §3. The professor's "basis" feature: an indexed knowledge base is a file any UniLearn user can import and use directly. Interchange contract: [`../architecture/data-model.md`](../architecture/data-model.md).

## UC-30 — Export / import

**Export**

1. `unilearn export <SUBJECT>` → zips the subject dir **minus `progress.db`** → `SUBJECT.unilearn`.
2. Bundle contains: manifest (pins), materials (original files — the importer's citations must open the right page; `--no-materials` to shrink), `store.db` (chunks, vectors, sparse, FTS, **verified decks/questions**, subject profile), `graph/` parquet.
3. Output states plainly: *"this bundle contains everything indexed in this subject."* (No source filtering — decided; personal state is protected by the file split, not a flag.)

**Import**

1. `unilearn import SUBJECT.unilearn` → manifest validated first (format version, schema version, counts).
2. **Embedding pin match** (model + hf_revision + dim + normalization — the global bge-m3 pin makes this the default) → vectors used as-is. Mismatch → offer re-embed from chunk text (local, free, minutes).
3. Zip-slip-safe extraction → `~/.unilearn/<SUBJECT>/` → **fresh empty `progress.db`** (your study state never comes from someone else).
4. Name collision → import under a new name, or replace with confirmation. **No merge in v1** — the honest merge is "import the materials and re-index the union."
5. Imported chunks, summaries, and the subject profile are **untrusted layer-4 content**; the profile keeps its size cap and no-authority rule.

**Why this is the cost model, not just a feature:** the two expensive artifacts — the graph (extraction dollars) and verified decks (verifier-loop tokens) — are exactly the exportable ones. One student pays; the course imports.

**Acceptance criteria**

- Export on machine A → import on machine B: `search` works immediately (no re-embedding on matched pins) and an `ask` citation opens the correct page of the bundled PDF.
- An import with a mismatched embedding pin triggers the re-embed path and then passes the same checks.
- A crafted bundle with path-escaping entries is rejected; importer's `progress.db` is untouched by any import.
- Importing a bundle never overwrites an existing subject without explicit confirmation.
