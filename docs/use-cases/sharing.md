# Use Case: Sharing Knowledge Bases (UC-30)

Detail for [`groundly-spec.md`](../groundly-spec.md) §3. The professor's "basis" feature: an indexed knowledge base is a file any Groundly user can import and use directly. Interchange contract: [`../architecture/data-model.md`](../architecture/data-model.md).

## UC-30 — Export / import

**Export**

1. `groundly export <SUBJECT>` → zips the subject dir **minus `progress.db`** → `SUBJECT.groundly`.
2. Bundle contains: manifest (pins), materials (original files — the importer's citations must open the right page; `--no-materials` to shrink), `store.db` (chunks, vectors, sparse, FTS, the topic tree). Bundles from before decision 37 also carry `graph/`; import ignores it.
3. Output states plainly: *"this bundle contains everything indexed in this subject, including its topic map."* (No source filtering — decided; personal state is protected by the file split, not a flag.)

**Import**

1. `groundly import SUBJECT.groundly` → manifest validated first (format version, schema version, counts).
2. **Embedding pin match** (model + hf_revision + dim + normalization — the global bge-m3 pin makes this the default) → vectors used as-is. Mismatch → offer re-embed from chunk text (local, free, minutes).
3. Zip-slip-safe extraction → `~/.groundly/<SUBJECT>/` → **fresh empty `progress.db`** (your study state never comes from someone else).
4. Name collision → import under a new name, or replace with confirmation. **No merge in v1** — the honest merge is "import the materials and re-index the union."
5. Imported chunks are **untrusted layer-3 content**.
6. A bundle exported before decision 38 (its `store.db` still has the deck tables) is refused with a message to re-index from its materials.
7. The topic tree travels inside `store.db` and needs no manifest field. A bundle with no tree tables (exported by an older Groundly), with dangling tree references, or with a tree deeper than two levels is refused; the importer sees every node as "Not started" (decision 41).

**Why this is worth sharing:** the index is the expensive artifact — extraction, OCR and embedding take minutes to hours per subject — and it is exactly the exportable one. One student indexes; the course imports.

**Acceptance criteria**

- Export on machine A → import on machine B: `search` works immediately (no re-embedding on matched pins) and a `search` citation opens the correct page of the bundled PDF.
- An import with a mismatched embedding pin triggers the re-embed path and then passes the same checks.
- A crafted bundle with path-escaping entries is rejected; importer's `progress.db` is untouched by any import.
- A bundle's topic tree is shown by `groundly serve` after import, and a title containing markup renders as text.
- Importing a bundle never overwrites an existing subject without explicit confirmation.
