# P2 — Import/Export (`groundly export` / `groundly import`)

## Context

P1 (indexing) is closed; branch `import-export` is clean and ready for P2. Per spec §8: **P2 = zip + manifest validation + re-embed path**; gate = "export on machine A, import on machine B, citations open the right page." The design is fully decided in docs — [UC-30](docs/use-cases/sharing.md), [data-model §Export/import](docs/architecture/data-model.md), [security §1 import trust boundary](docs/infrastructure/security.md). This plan is pure implementation; no doc changes needed.

**Current state:** export/import is greenfield — zero zip/archive code exists. But the foundations are done and get reused wholesale:
- `core/manifest.py` — `Manifest`/`Embedding` pydantic models, `FORMAT_VERSION=1`, bge-m3 pin, `sync_counts`
- `core/subject.py` — `Subject` (`.materials_dir`, `.store_db_path`, `.progress_db_path`, `.manifest_path`), `init_subject`
- `core/paths.py` — `groundly_home()`, `subject_dir()`, `validate_subject_name` (path-safe names)
- `core/store.py` — `connect()` (enforces `PRAGMA user_version` refusal — the imported-DB schema check for free), `create_progress()`
- `cli/app.py` — `app`, `console`, `_fail`, `_subject_checked`
- `llm/embeddings.py` — bge-m3 embedder (re-embed path only, lazy)

## Shape: 3 new files + 1 line

### 1. `groundly/core/bundle.py` (new, ~150 lines, stdlib `zipfile` only)

- `BundleError(RuntimeError)` — every failure names its specific cause; CLI maps to `_fail`.
- `export_subject(subj, out_path, include_materials=True, on_file=None)`:
  - First `store.connect(subj.store_db_path)` + `PRAGMA wal_checkpoint(TRUNCATE)` — WAL sidecars hold committed rows; without this the zipped store.db can be stale.
  - Zip from an **allowlist**: `manifest.json`, `store.db`, `materials/**` (unless excluded), `graph/**` if present. Never a directory rglob — `progress.db` and `-wal`/`-shm` are structurally unreachable. Module docstring states the privacy invariant; the string "progress" appears nowhere in this file (a test greps for it).
- `read_manifest(zf)` — `Manifest.model_validate_json(zf.read("manifest.json"))`; reject `format_version > FORMAT_VERSION` ("created by a newer groundly — upgrade") and negative counts.
- `validate_entries(zf)` — zip-slip gate, **reject not sanitize** (AC wording): absolute paths, `..` in `PurePosixPath(name).parts`, backslash/drive letter, symlinks (`stat.S_ISLNK(info.external_attr >> 16)`), and any entry outside the export allowlist (also blocks a smuggled `progress.db`). Error names the offending entry.
- `extract_bundle(bundle_path, dest_dir, on_file=None)` — manifest + entry validation **before** anything touches disk, then extract with per-file callback.
- `pin_matches(manifest)` — `manifest.embedding == Embedding()` (pydantic equality covers model+revision+dim+dtype+normalized in one line).
- `check_counts(store_db_path, manifest)` — opens via `store.connect` (runs the `user_version` refusal), compares manifest counts to actual `COUNT(*)`; mismatch = "bundle is damaged".
- `re_embed(store_db_path, embedder, on_progress=None)` — one transaction: delete `vectors` + `sparse_terms`, batch `chunks` text (32) through the embedder, reinsert at rowid=chunk id. Embedder passed as a parameter — no LLM import in `core/`, tests inject the existing `StubEmbedder`.

### 2. `groundly/cli/sharing.py` (new, ~100 lines, `subjects.py` pattern)

- `groundly export SUBJECT [-o PATH] [--no-materials]` — `_subject_checked`, per-file progress, default output `./SUBJECT.groundly`, then prints the doc-required line: *"this bundle contains everything indexed in this subject."*
- `groundly import BUNDLE [--as NAME] [--force]` (`@app.command("import")`, function `import_`; option `--as` → param `as_name`). Flow:
  1. Validate manifest from the zip (nothing extracted yet).
  2. `name = as_name or manifest.subject`; `validate_subject_name(name)`.
  3. Collision: without `--force` → `typer.confirm("subject 'X' exists — replace it? (its progress and notes are deleted)", abort=True)`; `--as` sidesteps; never silent.
  4. Extract into `tempfile.mkdtemp(dir=groundly_home()/".imports")` — dot-parent keeps half-imports out of `discover_subjects()`; same filesystem makes the final rename atomic.
  5. `check_counts` (includes the imported-store `user_version` check).
  6. Pin mismatch → confirm re-embed (local, free, minutes; `abort=True`) — only then `from groundly.llm.embeddings import ...` (zero-key path never imports it); rewrite `manifest.embedding = Embedding()` after.
  7. `manifest.subject = name`; save manifest; `store.create_progress(...)` — **fresh empty progress.db**; ensure `materials/` exists (`--no-materials` bundles).
  8. On replace: rmtree the old subject only now; rename tmp → `subject_dir(name)`.
  - Any failure → rmtree tmp, `_fail(cause)`. Nothing half-installed; existing subject untouched until step 8.

### 3. `groundly/cli/__init__.py` — add `sharing` to the existing registration import.

### 4. `tests/test_bundle.py` (new) — mapped to UC-30 acceptance criteria

Conventions: existing `subject` fixture (GROUNDLY_HOME → tmp), real SQLite, `StubEmbedder`, seed via `SQLiteSubjectStore.add_indexed` + `sync_counts` (no pipeline).

- **AC1 roundtrip**: export → repoint GROUNDLY_HOME ("machine B") → import → materials byte-identical, chunks/vectors/sparse row counts match, fresh empty progress.db. (Real `search` lands in P3; row-count equality is the honest P2 proxy.)
- **AC2 pin mismatch**: rewrite `hf_revision` inside the bundle's manifest → confirm "y" with StubEmbedder → vectors regenerated, manifest pin updated; "n" → nothing installed.
- **AC3 hostile bundle + privacy**: crafted zips (`../evil.txt`, absolute path, symlink entry, smuggled `progress.db`) each rejected naming the entry; importer's progress.db bytes unchanged. Structural privacy test: garbage-fill progress.db → export still succeeds (proves it's never opened) + namelist excludes it + grep-assert `"progress"` absent from `bundle.py`.
- **AC4 no overwrite**: collision + "n" → exit ≠ 0, original untouched; `--force` replaces; `--as` yields both subjects.
- Extras: bundled store.db with `user_version=99` refused (mirrors `test_store.py::test_refuses_newer_schema`); missing manifest; `format_version=2`; `--no-materials`.

## Deferred (say so, don't build)

- `--yes` non-interactive re-embed approval — prompt-only in v1.
- Merge (docs: no merge in v1), deck/graph content handling (P5/P6 — `graph/**` inclusion is already conditional), real `search` assertion in AC1 (P3).

## Verification

1. `pytest` — full suite green including new `test_bundle.py`.
2. End-to-end: `GROUNDLY_HOME=/tmp/a groundly init T && groundly index T <pdf> && groundly export T` → `GROUNDLY_HOME=/tmp/b groundly import T.groundly` → `groundly list` shows counts; bundle unzips to exactly manifest + store.db + materials/.
3. Review with `security-reviewer` (import trust boundary) and `spec-guardian` before the phase gate; leave changes uncommitted for Paul.
