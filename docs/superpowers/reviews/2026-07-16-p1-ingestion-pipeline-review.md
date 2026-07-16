# Adversarial review: P1 ingestion pipeline (2026-07-16)

Verdict: MERGE WITH FIXES

Scope: uncommitted tree on `ingestion-pipeline` — init/index/list/remove CLI, core stores,
Docling subprocess extraction, bge-m3 embedding. Contracts: UC-01, UC-03. Fast suite:
44 passed. Reproductions run against the real pipeline (stub embedder, real extraction
worker subprocess); repro script in session scratchpad (`repro.py`).

No data-corruption or grounding/privacy-invariant violations found; the blockers-in-spirit
are crash paths that violate the "failure messages name the cause, never generic errors"
convention, plus one false-success report.

## Findings

### F1 — Concurrent index of the same file crashes the whole run with an unhandled IntegrityError [severity: high]
- Where: `groundly/ingestion/pipeline.py:149-152` (and the `extraction_failed` insert at 131-136); `groundly/cli/__init__.py` catches only `(RuntimeError, ValueError)`
- Failure scenario: two `groundly index` runs (or CLI + a future MCP writer — the rules
  explicitly say they share store.db) race on the same new file. Both read
  `hash_status()` before either commits; the loser's `INSERT INTO materials` hits the
  `sha256 UNIQUE` constraint and `sqlite3.IntegrityError` propagates out of
  `index_paths`, aborting the run for every remaining file and dumping a raw traceback.
  WAL + busy_timeout do not help — this is a read-then-write race, not a lock conflict.
- Evidence: reproduced — simulated the second writer inside a stubbed `extract`;
  `index_paths` crashed with `IntegrityError: UNIQUE constraint failed: materials.sha256`.
- Cheap fix shape: catch `IntegrityError` in `_index_one` and report the file as
  `skipped_duplicate` (someone else indexed it — that is what the constraint proves).

### F2 — `list`/`remove` with an invalid subject name print a traceback instead of the error [severity: medium]
- Where: `groundly/cli/__init__.py:130` (`list_`) and the equivalent `sdir = subject_dir(subject)` in `remove`
- Failure scenario: `groundly list ../evil` → `subject_dir` raises `ValueError`, which
  `list_`/`remove` never catch (only `init` and `index` route it through `_fail`). The
  user gets a multi-screen rich traceback for a typo. Violates the conventions rule that
  failures name the cause cleanly.
- Evidence: reproduced — ran `uv run groundly list "../evil"`; raw traceback in the
  terminal. Same class (unverified): `list SUBJECT` when `store.db` was deleted but
  `manifest.json` survives → `sqlite3.OperationalError: no such table` traceback.

### F3 — Indexing a file that already lives in `materials/` crashes the run with SameFileError [severity: medium]
- Where: `groundly/ingestion/pipeline.py:74` (`shutil.copy2` in `_copy_to_materials`, called outside every try block in `_index_one`)
- Failure scenario: an orphaned file in `materials/` — precisely the state a Ctrl-C
  between copy (line 147) and commit (line 148) leaves behind, which UC-01 A4 declares
  recoverable — then `groundly index ~/.groundly/SUBJ/materials/`. The file's hash is
  not in the DB, so it reaches `_copy_to_materials`, `copy2(src, src)` raises
  `shutil.SameFileError`, and the whole run dies (not caught by the CLI's
  `(RuntimeError, ValueError)` either). "Interrupted run resumes without corruption"
  holds for the DB but the resume path can crash on its own leftovers.
- Evidence: reproduced — placed an un-indexed file in `materials/`, indexed it,
  `SameFileError` aborted `index_paths`.

### F4 — After a transient failure, a same-content sibling in the same run is reported "skipped (already indexed)" with nothing stored [severity: medium]
- Where: `groundly/ingestion/pipeline.py:117` (`known[sha] = "indexed"` runs unconditionally after `_index_one`, including for `EXTRACTION_FAILED` and `ERROR` results)
- Failure scenario: `a.txt` and `b.txt` have identical content; embedding `a.txt` fails
  transiently (`ERROR`, deliberately no row). `b.txt` is then reported
  `skipped (already indexed)` — a false success; zero rows exist. The next run recovers,
  but this run's output lies, and a single-run user walks away believing the content is
  indexed. Same line also mislabels a within-run twin of an `extraction_failed` file.
- Evidence: reproduced — output was `a.txt: error`, `b.txt: skipped_duplicate
  (already indexed)`, materials table count 0.
- Cheap fix shape: `known[sha] = result.status` (or only set when `INDEXED`).

### F5 — `extraction_failed` is not terminal: a scanned PDF is re-extracted on every re-run and the "idempotent" run exits 1 forever [severity: medium]
- Where: `groundly/ingestion/pipeline.py:113-114` (unconditional delete + retry of every `extraction_failed` hash); `groundly/cli/__init__.py` (`if failed: raise typer.Exit(code=1)`)
- Failure scenario: the UC-01 headline workflow — re-run `index` on the course folder
  each week. One scanned PDF in the folder means every weekly run deletes its row,
  re-runs Docling on it (up to the 300 s timeout, easily minutes of layout-model work),
  re-records the same failure, and exits 1 — permanently. UC-01 A1 calls
  `extraction_failed` a terminal state and the pipeline's own constant comment says
  "terminal"; the retry loop contradicts both. Retry-on-transient is a fine goal, but a
  byte-identical file that failed with "scanned PDF — not supported" cannot succeed next
  time.
- Evidence: code-traced (unverified by run — the loop is unconditional on status, no
  distinction between deterministic and transient extraction failures). The existing
  retry test (`test_empty_file_fails_cleanly_and_is_retried`) only covers the
  changed-content case, which would work via the new hash anyway.

### F6 — UC-01's first acceptance criterion (real PDF → correct page attribution + heading paths) has no test anywhere; the test docstring claims otherwise [severity: medium]
- Where: `tests/test_pipeline.py:1-3` (docstring: "Real-model and real-PDF checks are @pytest.mark.slow"); `tests/test_slow_models.py` (contains only the embedder contract test)
- Failure scenario: the page-attribution logic (`extract_worker.py:44-49`, takes
  `prov[0].page_no` of the first doc item) and heading-path assembly ship with zero
  coverage against a real PDF — the exact acceptance criterion the citation guarantee
  (grounding rules: citations resolve to document + page) will later stand on. If
  Docling's chunk→provenance mapping is off by one, nothing in this suite notices.
- Evidence: read both test files; no PDF fixture, no slow PDF test. The `.md` heading
  test exists but pages are `None` for markdown.

### F7 — `remove` treats the identifier as a LIKE pattern: `%` (or "") matches and deletes materials [severity: low]
- Where: `groundly/core/store.py:120-125` (`sha256 LIKE ident + '%'` with unescaped ident)
- Failure scenario: `groundly remove SUBJ '%' -y` on a one-material subject deletes it
  (reproduced — exit 0, "removed only.pdf"); with several materials it "helpfully" lists
  all of them as candidates. `_` in the ident wildcards too. Local tool, user-typed input
  — low, but the disambiguator's contract says "sha256 prefix", and sha prefixes are
  `[0-9a-f]`; a one-line character check (or `ESCAPE`) closes it.
- Evidence: reproduced.

### F8 — File copy and DB row are not atomic; crash windows leave orphan files [severity: low]
- Where: `groundly/ingestion/pipeline.py:147-148` (copy before the transaction); `groundly/cli/__init__.py` remove path (unlink after commit)
  (self-heals on re-run of the original path, but see F3 for how the orphan can then

### F9 — UC-01 main-flow step 4 (corpus-hash graph offer + cost estimate) is absent, but `remove` already prints a note referencing it [severity: low]
- Where: `groundly/ingestion/pipeline.py` (no corpus-hash logic); `groundly/cli/__init__.py` remove ("the graph rebuilds on the next corpus-hash-triggered index run")
- Failure scenario: none today (no graph exists to go stale). Flagged so the gap is
  deliberate, on the record, and the user-facing note stops being a promise the code
  can't keep if graph work lands in a different shape.
- Evidence: read; presumably deferred by phasing (the spec doc under docs/superpowers/specs
  was out of review scope).

## What I tried and could not break

- `remove` leaves no retrievable rows in any channel (materials/chunks/vectors/sparse/FTS) — schema triggers + explicit vec0 delete verified by test and by reading the trigger DDL.
- Hash-skip idempotency: unchanged folder re-embeds nothing, one new file embeds alone (real subprocess extraction in the fast tests, not a mocked worker).
- Empty/whitespace file → `EXIT_NO_TEXT` → "no extractable text"; `.pdf` maps to "scanned PDF — not supported" per A1.
- Worker crash containment (A2): nonzero exit → `extraction_failed` with last stderr line, run continues.
- Newer-schema refusal, WAL + busy_timeout + foreign_keys on every `connect()` — including the store the CLI opens.
- Two different-content files with the same filename in one run → second stored as `stem-<sha8>` — no clobber.
- The CLI index test monkeypatches `pipeline.index_paths` but the CLI resolves it at call time via the module attribute — the patch is effective, not a lying test.
- Zero-key operation: nothing in init/index/list/remove touches an LLM provider; only HF downloads (allowed by the privacy rules).
- Lazy loading: no bge-m3/Docling import at module import; weights-vs-tokenizer-only cache distinction in `BgeM3Embedder._load` handles the worker having cached only the tokenizer.
- Chunk rowid reuse after `remove` (no AUTOINCREMENT): vectors and FTS stay consistent because both are purged in the same transaction that deletes the chunks.
- `manifest.json` counts stay in sync after index (per file) and remove (UC-03 §3); `extraction_failed` rows correctly excluded from the materials count.
- vec0/FTS writes are inside the per-file transaction (shadow tables are ordinary tables) — Ctrl-C loses at most the in-flight file's rows, per A4.
