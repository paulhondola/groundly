# vec0 `material_id` column — design (small change)

## Intent

`vectors` (sqlite-vec `vec0`) carries no material, so two paths work around it: a
material-scoped dense search runs a second, plain-scan query (`rowid IN (SELECT id FROM
chunks …) ORDER BY vec_distance_l2`), and `remove_material` deletes vectors one chunk id at
a time. A `material_id` metadata column gives both paths one statement. This is a cleanup,
not a speedup that matters: the scoped search went from 12.0 to 4.3 ms on 30k vectors and
only CLI `search -m` scopes; unscoped search is unchanged (7.7 vs 7.8 ms, identical top 50).

It came out of comparing a move to Turso (pyturso 0.8.4) or LanceDB 0.41: Turso needs two
experimental flags (`multiprocess_wal`, `index_method`), drops FTS5 and makes files with a
full-text index unreadable by stock SQLite; LanceDB has no foreign keys or cross-table
transactions, so it could only sit beside SQLite. Both rejected for now; Paul picked staying
on sqlite-vec with this column.

## Goals / non-goals

- **Goal:** `vectors` = `vec0(embedding float[1024], material_id integer)`; one dense query
  shape; a one-statement vector delete; existing stores and bundles keep working.
- **Non-goals:** partition key, ANN index, any change to the sparse or BM25 channels (they
  already filter through `chunks`), the MCP surface (still no material filter), the
  embedding pin.
- **Anti-goals:** a store that loses or mismatches a vector during the upgrade; a bundle
  whose vectors claim a material their chunk is not in.

## Approaches

1. **Metadata column** (picked) — same speed and size (+0.2% pages) as today unscoped, 3×
   faster scoped, `DELETE FROM vectors WHERE material_id = ?` works.
2. **Partition key** — scoped search 0.2 ms, but each material gets its own vec0 storage;
   with many small materials the unscoped search was 5× slower (37.3 ms) and the store 6.6×
   larger. Rejected: courses are many small files.
3. **Leave it** — the workarounds are correct and exact; rejected only because the column is
   cheap.

Migration: **upgrade in place** (Paul's pick) over refusing version-2 stores, so indexed
courses survive without re-running extraction and OCR.

## Design

- **Schema version 3.** `STORE_USER_VERSION = 3`; fresh stores create the new `vectors`.
- **Upgrade, in `store.connect`:** a version-1 store runs the existing question-table step,
  then the vector step; a version-2 store runs the vector step. Both in one `BEGIN
  IMMEDIATE` transaction, version re-read under the lock (the existing `_upgrade` pattern).
  The vector step copies `(rowid, embedding, chunks.material_id)` out (a `TEMP` table, so no
  `vectors_`-prefixed object lands in the file), drops `vectors`, creates the new one, copies
  back. Vectors with no chunk are dropped in the copy. A failure rolls back and leaves the
  version-2 store as it was.
- **`dense_search`:** one query, `embedding MATCH ? AND k = ?` plus `AND material_id = ?`
  when scoped. The plain-scan branch and its docstring go.
- **Writers pass `material_id`:** `Store.add_material` and `bundle.re_embed` (which looks it
  up from `chunks`).
- **`remove_material`:** `DELETE FROM vectors WHERE material_id = ?` in the same transaction.
- **Import check:** a bundle whose `vectors.material_id` differs from its chunk's
  `material_id` is rejected with a specific message. A version-2 bundle is upgraded by
  `store.connect` before `schema_problem` runs, as version-1 bundles are today.
- **No manifest `FORMAT_VERSION` bump** (my call): an older groundly already refuses a
  version-3 store through `user_version` with "upgrade groundly".

## Docs in the same change (`/decision`, decision 56)

- `docs/groundly-spec.md` §7: decision 56; decision 40's "vec0 takes no material predicate
  without a schema change" note points to it.
- `docs/architecture/data-model.md`: `vectors` row, schema version 3, the second upgrade.
- `.claude/rules/architecture.md`: "The one in-place upgrade (version 1 to 2) only adds empty
  tables" becomes: upgrades add empty tables or rebuild `vectors` from its own rows; any
  opener runs them, the page included.
- `docs/tech-stack/tech-stack.md`: Turso and LanceDB findings in one line each under the
  storage row's alternatives.

## Left to the builder

- Whether vec0 0.1.9 supports `ALTER TABLE … RENAME` (would skip the copy-back); the TEMP
  copy is the safe default.
- Test shapes: fresh store, v1→v3, v2→v3 (vectors and search results identical before and
  after), concurrent openers upgrading once, scoped search, one-statement removal, the
  mismatched-bundle rejection.
