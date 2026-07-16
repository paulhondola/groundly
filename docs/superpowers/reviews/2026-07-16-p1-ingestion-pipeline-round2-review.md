# Adversarial review: P1 ingestion pipeline, round 2 (2026-07-16)

Verdict: MERGE WITH FIXES (F1 and F2 required before merge)

Scope: full branch delta `main...ingestion-pipeline` (commits a54a790..60ecf19), with
specific attention to whether the fixes after the first review (60ecf19: ModelUnavailable /
EXIT_MODEL_UNAVAILABLE, relative-path resolution, IntegrityError race, SameFileError,
LIKE escaping, terminal extraction_failed) actually hold. Fast suite: 40 passed,
14 deselected. Slow pipeline suite run locally: 13 passed in 92 s. All high/medium
findings below were reproduced with scripts against the real pipeline (real extraction
worker where relevant), not just code-traced.

Prior-round fixes that do hold: relative paths (worker gets `path.resolve()`, regression
test exists), IntegrityError on the *indexed* insert path, orphan-in-materials resume,
LIKE wildcard escaping, terminal extraction_failed with skip-and-report, transient-sibling
misreport, invalid-subject-name tracebacks in list/remove.

## Findings

### F1 — `remove` of an `extraction_failed` row silently deletes another material's stored file [severity: high]
- Where: `unilearn/cli/__init__.py:192-194` (unlink by `target["filename"]`); root cause
  `unilearn/ingestion/pipeline.py:154-157` (failed rows record `path.name` with no
  collision handling, unlike the indexed path's `_copy_to_materials` suffixing)
- Failure scenario: `lec.pdf` (good) is indexed → stored as `materials/lec.pdf`. A
  different file also named `lec.pdf` (e.g. the scanned version) fails extraction → row
  `(filename='lec.pdf', status='extraction_failed')`, no copy made. User runs
  `unilearn remove S <sha-prefix-of-failed> -y` (the sha prefix is forced — the filename
  is ambiguous). The failed row is removed, then the unlink at cli/__init__.py:193 hits
  `materials/lec.pdf` — the *indexed* material's file. Exit 0, "removed lec.pdf". The
  indexed row survives but its citation target / export payload is gone, silently.
  Grounding rules make original files the citation targets; this is silent data loss.
- Evidence: reproduced (scratchpad `repro_remove.py`) — exit 0, indexed row still
  present, stored file deleted.
- Fix shape: only unlink when the removed row's status is `indexed` (failed rows never
  copied anything), and/or verify no other row references the same filename.

### F2 — Docling model-fetch failure is recorded as *terminal* `extraction_failed`; an offline first run permanently poisons every PDF/DOCX/PPTX/MD [severity: high]
- Where: `unilearn/ingestion/extract_worker.py:50` (`DocumentConverter().convert` runs
  *before* `_load_tokenizer`, outside any EXIT_MODEL_UNAVAILABLE handling);
  `unilearn/ingestion/extract.py:80-82` (exit 1 → `ExtractionFailure("parser failed: …")`)
- Failure scenario: fresh machine (or cleared HF cache) with no network — exactly the
  transient environment 60ecf19 set out to handle. `.txt` files correctly exit 4 →
  retryable `error`, no row. But for any Docling-suffix file, docling's own layout-model
  download raises inside `convert()` → worker exits 1 → parent records a **terminal**
  `extraction_failed` row. Because extraction_failed is now terminal (pipeline.py:122-131),
  every subsequent run — network restored, models cached — reports
  "failed previously … remove to retry" forever. One offline `unilearn index` on a course
  folder requires a manual `remove` per document to recover. The recorded error message is
  also a raw huggingface_hub traceback line, not a named cause (conventions violation).
- Evidence: reproduced — ran the worker on a real PDF with `HF_HOME=<empty>` +
  `HF_HUB_OFFLINE=1`: exit code 1 (`LocalEntryNotFoundError` on stderr); same environment
  with a `.txt`: exit code 4. The 60ecf19 fix covers only the bge-m3 tokenizer load.
- Fix shape: wrap the docling import/convert model-acquisition failure classes (or probe
  the artifact cache) in the same EXIT_MODEL_UNAVAILABLE path.

### F3 — The `extraction_failed` INSERT still loses the concurrent-run race and aborts the whole run [severity: medium]
- Where: `unilearn/ingestion/pipeline.py:152-158` — the IntegrityError catch added in
  60ecf19 wraps only `_write_indexed` (line 170-176), not the failure-path insert. The
  first review's F1 named both insert sites.
- Failure scenario: two runs (CLI + MCP share store.db per the architecture rules) both
  hit the same failing file; the loser's `INSERT … 'extraction_failed'` violates
  `sha256 UNIQUE` → `sqlite3.IntegrityError` escapes `index_paths`, every remaining file
  in the run is abandoned, and the CLI (which catches only `RuntimeError, ValueError`,
  cli/__init__.py:109) dumps a raw traceback.
- Evidence: reproduced (scratchpad `repro_race_fail.py`) — second file in the run never
  indexed, `CRASH: IntegrityError UNIQUE constraint failed: materials.sha256`.

### F4 — The entire pipeline test suite is excluded from CI; CI green proves nothing about this branch's core deliverable [severity: medium]
- Where: `tests/test_pipeline.py:16` (`pytestmark = pytest.mark.slow`) +
  `pyproject.toml:56` (`addopts = "-m 'not slow'"`); commit baec811
- Failure scenario: any regression in `pipeline.py`, `extract.py`, `extract_worker.py`,
  or `embeddings.py` ships with a green CI run. Default `uv run pytest`: 40 passed,
  **14 deselected** — all 13 pipeline tests among them. The spec explicitly designed the
  pipeline tests as fast tests ("StubEmbedder … no model downloads in CI"); the file's
  own docstring still claims "no model download" while the marker exists precisely
  because the extraction worker needs the real bge-m3 tokenizer. UC-01's testable
  acceptance criteria (idempotent re-run, terminal states, resume) are never checked by
  CI. Pure-logic tests (symlink skip, unsupported extension, uninitialized subject) need
  no worker at all and could run in CI today; the extraction-dependent ones could with a
  stubbed `extract`.
- Evidence: ran `uv run pytest -q` → 40 passed, 14 deselected; read the marker and CI
  workflow (plain `uv run pytest`).

### F5 — `list` crashes with raw tracebacks on a corrupt manifest or a missing store.db; `connect()` silently creates an empty DB [severity: medium]
- Where: `unilearn/cli/__init__.py:140` (`Manifest.load` in the list-all loop, uncaught),
  `:146` (`store.connect` + `list_materials`, uncaught `OperationalError`);
  `unilearn/core/store.py:64` (`sqlite3.connect` creates the file when absent;
  user_version 0 passes the ≤ known check)
- Failure scenario: (a) a manifest.json truncated by a crash/Ctrl-C (see F7 — the writer
  is not atomic) makes `unilearn list` die with a pydantic `ValidationError` traceback
  and list *no* subjects, including healthy ones. (b) `store.db` deleted while
  manifest.json survives → `unilearn list S` throws `sqlite3.OperationalError: no such
  table: materials` — and as a side effect leaves behind a freshly created, schema-less
  `store.db`, converting a recoverable state ("re-init") into a confusing one. Both
  violate "failure messages name the cause specifically, never generic errors".
- Evidence: reproduced (scratchpad `repro_list.py`/`repro_list2.py`) — ValidationError /
  OperationalError as `result.exception`, empty output, bogus store.db created.

### F6 — `_copy_to_materials` sits outside every try block: any copy OSError aborts the whole run [severity: low]
- Where: `unilearn/ingestion/pipeline.py:169` (called between the embed try and the
  write try in `_index_one`)
- Failure scenario: disk full, permission error on `materials/`, or — reproduced — a
  hard link of an already-stored file (the 60ecf19 orphan fix compares `resolve()`
  paths, which does not catch hard links; `shutil.copy2` compares inodes) →
  `shutil.SameFileError`/`OSError` escapes `index_paths`, remaining files abandoned, raw
  traceback (not in the CLI's catch list).
- Evidence: hard-link case reproduced (scratchpad `repro_hardlink.py`) —
  `CRASH: SameFileError`. ENOSPC/EACCES unverified — plausible, same code path.

### F7 — `Manifest.save` is a non-atomic truncate-then-write; interruption or a concurrent run corrupts manifest.json [severity: low]
- Where: `unilearn/core/manifest.py:63` (`path.write_text`), called per-file from
  `index_paths` (pipeline.py:137) and from `remove`
- Failure scenario: Ctrl-C during the write (index calls it after *every* file, so the
  window recurs constantly), or two concurrent `index` runs interleaving truncate/write,
  leaves a partial JSON. Everything that loads the manifest then fails — including
  `unilearn list` for *all* subjects (F5a). A4's "interrupted run resumes without
  corruption" holds for the DB but not for the manifest sitting next to it.
- Evidence: unverified — code-traced; the downstream crash on a truncated manifest is
  reproduced (F5a).

### Carried over, still open (from the first review, acknowledged but not resolved)
- UC-01 acceptance criterion #1 (real PDF → correct page attribution + heading paths)
  has no automated test on any marker; `tests/test_pipeline.py:3` now documents it as
  "verified manually per release". The page-attribution logic
  (extract_worker.py:59-63, `prov[0].page_no` of the first doc item) ships untested.
- UC-01 main-flow step 4 (corpus-hash graph offer + cost estimate) absent; `remove`
  still prints the note that references it (cli/__init__.py:196-200). Deferred by
  phasing — fine, but on the record.
- File-copy vs DB-row atomicity windows (orphan file on kill between copy and commit;
  stale file on kill between remove-commit and unlink) remain; the crash-on-resume
  symptom was fixed, the orphans themselves were not.

## What I tried and could not break

- Terminal-failure semantics for unchanged files: failed hash → skipped_failed with the
  stored error + "remove to retry"; changed content → new hash indexes (slow suite).
- Transient paths leave no row: embedder crash and ModelUnavailable both retryable; a
  later run indexes cleanly with no `remove` needed (test + read).
- Indexed-path IntegrityError race: stale hash snapshot → skipped_duplicate, run continues.
- `remove` leaves zero rows in materials/chunks/sparse/vectors/FTS (test + trigger DDL).
- LIKE wildcards neutralized: `find_materials` with `%`/`_` matches nothing (test).
- `list`/`remove`/`init` with `../evil` fail cleanly, exit 1, named cause (test run).
- Symlink *files* are skipped, including inside directories (rglob on 3.11/3.12 does not
  follow symlinked dirs; a symlinked dir passed explicitly is user-typed input).
- Layering: cli → ingestion → core/llm only; embedder constructed in `llm/`; zero-key —
  no provider touched anywhere in init/index/list/remove; lazy loading — no model or
  docling import at module import time.
- WAL + busy_timeout + foreign_keys on every `connect()`, and on `create_progress`;
  newer user_version refused (test).
- `zip(..., strict=True)` in `_write_indexed` inside the transaction: a length-lying
  embedder rolls back, no partial chunk rows.
- manifest counts exclude extraction_failed rows and stay synced after index/remove.
- Slow suite (13 pipeline tests) passes against the real extraction worker locally.
