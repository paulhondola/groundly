# vec0 `material_id` Column Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the sqlite-vec `vectors` table a `material_id` metadata column so a scoped dense search and a material's vector removal are each one statement, and upgrade version-1/2 stores and bundles to version 3 in place.

**Architecture:** One DDL constant for `vectors` shared by the fresh schema and the upgrade, so `schema_problem`'s exact-SQL comparison holds for upgraded stores. The upgrade extends the existing `_upgrade` (one `BEGIN IMMEDIATE` transaction, version re-read under the lock) with a vector-rebuild step through a `TEMP` table. Import gets one more shape check.

**Tech Stack:** Python 3.11+, stdlib `sqlite3`, sqlite-vec 0.1.9 (metadata columns), pytest.

**Spec:** `docs/superpowers/specs/2026-11-27-vec0-material-id-design.md`

## Global Constraints

- New DDL, verbatim: `CREATE VIRTUAL TABLE vectors USING vec0(embedding float[{EMBEDDING_DIM}], material_id integer)` — a metadata column, **not** `partition key`.
- `STORE_USER_VERSION = 3`. No manifest `FORMAT_VERSION` bump.
- Upgrade = one transaction under `BEGIN IMMEDIATE`; version re-read inside it; failure rolls back and leaves the old store untouched.
- The upgrade's copy table lives in the `temp` schema and must not start with `vectors_` (that prefix is reserved for sqlite-vec shadow tables in `_EXTENSION_OBJECTS`).
- Vectors whose rowid has no chunk are dropped by the upgrade.
- Comments/docstrings state intent and invariants, no history (`.claude/rules/conventions.md`).
- Work on a new branch `feat/vec0-material-id` cut from `main` in its own worktree (superpowers:using-git-worktrees). The main checkout holds uncommitted `codex/web-agent-switcher` work; do not touch it. First commit on the branch: this plan and its spec, copied from the main checkout.
- Gate before each commit: `uv run pytest -q tests/core tests/retrieval tests/cli`, `uv run ruff check`, `uv run pyrefly check`, `uv run lint-imports`.

## Review Focus

- **Search results identical across the upgrade:** a v2 store's `dense_search` (scoped and unscoped) returns the same ids before and after upgrading → pinned in Task 2.
- **Two processes open a v2 store at once:** exactly one rebuilds; the other sees version 3 and does nothing; no duplicate or lost vectors → pinned in Task 2 (second `_upgrade` call on an upgraded store).
- **A version-1 store (no question tables, old vectors):** both steps run in one transaction and land at version 3 → pinned in Task 2.
- **A v2 bundle with an orphan vector (rowid with no chunk):** import upgrades it and the orphan is gone, rather than failing on a NULL `material_id` → pinned in Task 2 (store level) and Task 3 (import).
- **A hostile v3 bundle whose vector names another material:** refused at import with a specific message → pinned in Task 3.

---

### Task 1: Schema v3, writers, single-query dense search, one-statement removal

**Files:**
- Modify: `groundly/core/store.py` (`_BASE_SCHEMA` vectors line, `STORE_USER_VERSION`, `SubjectStore.add_material`, `dense_search`, `remove_material`)
- Modify: `groundly/core/bundle.py` (`re_embed`)
- Test: `tests/core/test_store.py`, `tests/core/test_bundle.py`

**Interfaces:**
- Produces: `store._VECTORS_DDL: str` — the exact `CREATE VIRTUAL TABLE vectors …` statement (formatted with `EMBEDDING_DIM`), used by `_BASE_SCHEMA` and by Task 2's upgrade. `STORE_USER_VERSION = 3`.
- `dense_search(embedding, k, material_id=None) -> list[int]` and `remove_material(material_id) -> None` keep their signatures.

- [ ] **Step 1: Write the failing tests** in `tests/core/test_store.py`

```python
def test_every_vector_is_filed_under_its_chunks_material(two_materials, tmp_path):
    conn = store.connect(tmp_path / "store.db")
    try:
        assert conn.execute(
            "SELECT count(*) FROM vectors v JOIN chunks c ON c.id = v.rowid "
            "WHERE v.material_id IS NOT c.material_id"
        ).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM vectors").fetchone()[0] == \
            conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    finally:
        conn.close()


def test_a_fresh_store_is_version_3():
    assert store.STORE_USER_VERSION == 3
```

and in `tests/core/test_bundle.py` a `re_embed` test asserting the same zero-mismatch query after `bundle.re_embed(path, stub_embedder)` (reuse the file's existing stub embedder fixture). Use whatever fixture the file already has that seeds two materials; if `two_materials` does not expose `tmp_path`'s store path, open it the way neighbouring tests do.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/core/test_store.py -k "filed_under or version_3" tests/core/test_bundle.py -k re_embed`
Expected: FAIL (`no such column: material_id` / version 2).

- [ ] **Step 3: Implement**
  - `_VECTORS_DDL` constant; `_BASE_SCHEMA` uses it (keep the rowid comment, reworded: rowid = chunks.id; vec0 has no FK, so removal deletes by `material_id`).
  - `add_material`: `INSERT INTO vectors (rowid, embedding, material_id) VALUES (?, ?, ?)`.
  - `re_embed`: select `id, material_id, text` from `chunks`; insert all three.
  - `dense_search`: one statement — `SELECT rowid FROM vectors WHERE embedding MATCH ? AND k = ?` + `AND material_id = ?` only when `material_id is not None`, `ORDER BY distance`. Drop the plain-scan branch; docstring: exact KNN, scoped by the metadata column.
  - `remove_material`: replace the per-chunk loop with `DELETE FROM vectors WHERE material_id = ?`; docstring names it.

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q tests/core tests/retrieval tests/cli`
Expected: PASS except the existing upgrade tests that assert version 2 (`test_the_upgrade_leaves_a_store_another_process_already_upgraded`, `test_a_version_1_store_is_upgraded_in_place_keeping_its_rows`, the v1 bundle tests) — those are rewritten in Tasks 2–3. List them in the commit message.

- [ ] **Step 5: Commit**

```bash
git add groundly/core/store.py groundly/core/bundle.py tests/core/test_store.py tests/core/test_bundle.py
git commit -m "feat(store): file each vector under its material (schema v3)"
```

---

### Task 2: In-place upgrade to version 3

**Files:**
- Modify: `groundly/core/store.py` (`connect`, `_upgrade`)
- Test: `tests/core/test_store.py`

**Interfaces:**
- Consumes: `store._VECTORS_DDL`, `STORE_USER_VERSION = 3` (Task 1).
- Produces: `store._upgrade(conn)` upgrades from 1 or 2; `connect` calls it when `version < STORE_USER_VERSION`. Test helper `_downgrade_to_v2(path)` in `tests/core/test_store.py` (Task 3 has its own SQL equivalent).

- [ ] **Step 1: Write the test helpers and failing tests**

`_downgrade_to_v2(path)`: open with `sqlite3.connect`, `enable_load_extension(True)`, `sqlite_vec.load(conn)`; copy `vectors` (rowid, embedding) into a temp table, `DROP TABLE vectors`, `CREATE VIRTUAL TABLE vectors USING vec0(embedding float[1024])`, copy back, `PRAGMA user_version = 2`. `_downgrade_to_v1` calls `_downgrade_to_v2` first, then its existing drops.

```python
def test_a_version_2_store_is_upgraded_keeping_every_vector_and_result(two_materials, tmp_path):
    s, q, mid = ...  # store, a query vector, a material id from the fixture
    before = (s.dense_search(q, k=10), s.dense_search(q, k=10, material_id=mid))
    _downgrade_to_v2(tmp_path / "store.db")
    conn = s.connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert store.schema_problem(conn) is None
        assert conn.execute(
            "SELECT count(*) FROM vectors v JOIN chunks c ON c.id = v.rowid "
            "WHERE v.material_id IS NOT c.material_id").fetchone()[0] == 0
    finally:
        conn.close()
    assert (s.dense_search(q, k=10), s.dense_search(q, k=10, material_id=mid)) == before


def test_the_upgrade_drops_a_vector_with_no_chunk(two_materials, tmp_path):
    # after _downgrade_to_v2, insert a vector at rowid 999999 (no chunk) via a raw
    # sqlite_vec-loaded connection; open with store.connect; assert rowid 999999 is gone
    # and every other vector survived.
```

Update the existing ones: `test_a_version_1_store_is_upgraded_in_place_keeping_its_rows` asserts `== store.STORE_USER_VERSION` and `schema_problem(conn) is None`; `test_the_upgrade_leaves_a_store_another_process_already_upgraded` asserts `STORE_USER_VERSION` and that the vector count is unchanged by the second `_upgrade` call.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/core/test_store.py -k "upgrade"`
Expected: FAIL (version 2 store opens without rebuilding; `material_id` missing).

- [ ] **Step 3: Implement**
  - `connect`: `if version == 1:` → `if version < STORE_USER_VERSION:` (same `SchemaUpgradeError` wrapping).
  - `_upgrade`: under `BEGIN IMMEDIATE`, read `v`; if `v == 1` run the question schema; if `v <= 2` rebuild vectors; then set the version. Docstring: adds empty tables and rebuilds `vectors` from its own rows; another process may have upgraded while this one waited.
  - Rebuild, in order: `CREATE TEMP TABLE upgrade_vectors AS SELECT v.rowid AS id, v.embedding AS embedding, c.material_id AS material_id FROM vectors v JOIN chunks c ON c.id = v.rowid`; `DROP TABLE vectors`; `_VECTORS_DDL`; `INSERT INTO vectors (rowid, embedding, material_id) SELECT id, embedding, material_id FROM temp.upgrade_vectors`; `DROP TABLE temp.upgrade_vectors`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q tests/core tests/retrieval tests/cli tests/web/test_app.py -k "version or upgrade or search"`
Expected: PASS (`test_a_version_1_store_still_shows_its_map` included).

- [ ] **Step 5: Commit**

```bash
git add groundly/core/store.py tests/core/test_store.py
git commit -m "feat(store): upgrade version-1 and -2 stores to schema v3 in place"
```

---

### Task 3: Import upgrades old bundles and refuses misfiled vectors

**Files:**
- Modify: `groundly/core/bundle.py` (`check_store_shape`)
- Test: `tests/core/test_bundle.py` (`_edit_bundled_store`, `_DOWNGRADE`, new tests)

**Interfaces:**
- Consumes: Task 2's upgrade, reached through `store.connect` inside `check_store_shape`.

- [ ] **Step 1: Write the failing tests**
  - `_edit_bundled_store`: load sqlite-vec on its connection (`enable_load_extension(True)`, `sqlite_vec.load`) so statements can touch `vectors`.
  - Add `_VECTORS_TO_V2` statements (temp copy, drop, recreate as `vec0(embedding float[1024])`, copy back) and make `_DOWNGRADE` = `_VECTORS_TO_V2` + the existing question drops + `PRAGMA user_version = 1`. Existing v1 tests then exercise both steps.

```python
def test_a_version_2_bundle_is_upgraded_on_import(tmp_path, monkeypatch):
    # as test_a_version_1_bundle_is_upgraded_on_import, with *_VECTORS_TO_V2,
    # "PRAGMA user_version = 2"; after import: user_version == STORE_USER_VERSION and
    # `groundly search` (or SubjectStore.dense_search) returns the same ids as on side a.


def test_a_bundle_with_a_vector_filed_under_another_material_is_refused(tmp_path, monkeypatch):
    # seed two materials; _edit_bundled_store(..., "UPDATE vectors SET material_id = <other> WHERE rowid = <a chunk id>")
    _assert_refused(result, "bundle vector", "filed under a different material than its chunk")
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/core/test_bundle.py -k "version or filed_under"`
Expected: the misfiled test FAILS (import succeeds); the version tests pass or fail only on helper changes.

- [ ] **Step 3: Implement** in `check_store_shape`, after the chunk page check: `SELECT v.rowid AS id FROM vectors v JOIN chunks c ON c.id = v.rowid WHERE v.material_id IS NOT c.material_id LIMIT 1`; on a row raise `BundleError(f"bundle vector {row['id']!r} is filed under a different material than its chunk — rejected")`. (If vec0 rejects `UPDATE` of a metadata column in the test, build the misfiled bundle with `DELETE` + `INSERT` of that rowid instead.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q tests/core tests/cli`
Expected: PASS, all previously listed version-2 assertions now green.

- [ ] **Step 5: Commit**

```bash
git add groundly/core/bundle.py tests/core/test_bundle.py
git commit -m "feat(import): upgrade v2 bundles, refuse vectors filed under another material"
```

---

### Task 4: Decision 56 across the docs

**Files:**
- Modify: `docs/groundly-spec.md` (§7 decision 56; decision 40's vec0 note points to it), `docs/architecture/data-model.md` (`vectors` row, schema version 3 paragraph), `.claude/rules/architecture.md` (upgrade rule, see Global/spec), `docs/tech-stack/tech-stack.md` (storage alternatives: Turso and LanceDB one line each), `groundly/core/store.py` module docstring if it says anything version-specific.

- [ ] **Step 1: Run `/decision`** with: "vec0 `vectors` gains a `material_id` metadata column (schema v3); stores and bundles at v1/v2 upgrade in place by rebuilding `vectors` from its own rows; Turso (two experimental flags, no FTS5, FTS index unreadable by stock SQLite) and LanceDB (no FKs or cross-table transactions) evaluated and not adopted." Text for the rule line: "Upgrades add empty tables (v1→v2) or rebuild `vectors` from its own rows (v2→v3); any opener runs them, the page included."

- [ ] **Step 2: Verify** `grep -rn "no material predicate\|only adds empty tables\|Schema version 2" docs .claude/rules` returns nothing stale.

- [ ] **Step 3: Full gate, then review** — `uv run pytest -q`, `uv run ruff check`, `uv run pyrefly check`, `uv run lint-imports`; dispatch `spec-guardian` and `security-reviewer` on the branch diff (import boundary touched).

- [ ] **Step 4: Commit**

```bash
git add docs .claude/rules groundly/core/store.py
git commit -m "docs: decision 56 — vec0 material_id column and the v3 upgrade"
```
