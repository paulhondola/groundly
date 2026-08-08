# Adversarial review: P5 retrieval eval harness (2026-08-04)

Reviewed `git diff 586a219 28cdd19` (PR #29, branch `p5-eval-harness`, 21 files, +1628/-64),
against `docs/architecture/retrieval.md`, `docs/groundly-spec.md` §7 decision 27 and
`.claude/rules/`. Full suite re-run: 568 passed. All reproductions below used
`.venv/bin/python` against the real `~/.groundly/apd` index (187 materials / 1,193 chunks /
graph built).

Verdict: BLOCK

Not because the harness is broken — it works, and its headline vector table reproduces to
three decimal places (see "What I tried and could not break"). Because two figures already
written into `docs/groundly-spec.md` §7 — the source of truth other work will cite — are not
supported by the code that produced them (F1, F2), and one measured cost figure is off by
~22% (F7). Decision 27's own text says a confidently-stated cost figure has now been the
defect twice; this is the third. The fixes are small: correct three doc claims, make
`source_file` a list, warn when it does not resolve.

## Findings

### F1 — `graph-global`'s MRR measures SQLite rowid order, not retrieval rank [severity: high]

- Where: `groundly/retrieval/graph.py:304` (`chunk_ids = sorted(resolved)`) →
  `graph.py:142` (`score=1.0 / (rank + 1)`) → `groundly/eval/metrics.py:27`
  (`reciprocal_rank`); published in `docs/architecture/retrieval.md` ("MRR of 0.02 is what
  exposes it") and `docs/groundly-spec.md` decision 27.
- Failure scenario: global search's citation join collects chunk ids into a `set` and emits
  them **sorted ascending**. There is no relevance order in that list at all — position 1 is
  simply the lowest chunk id, i.e. the first chunk of the first file ingested.
  `reciprocal_rank` then reports `1 / (position of the lowest-numbered gold chunk)`. A
  question labelled on `Curs 1` (chunk ids 26–60) scores ~1/30; the identical question
  labelled on `Curs 11` (chunk ids ~500) scores ~1/500. The per-class slice that lands in a
  thesis table reads `factoid 0.0096 / multi-hop 0.0103 / global 0.0432`, which invites
  "graph-global is 4x better on global questions" — the real content of that number is
  "global questions happen to be labelled on lower-numbered chunks."
- Evidence: reproduced the entire published graph-global row **with no LLM call whatsoever**,
  from the parquet artifacts plus the gold labels, because the returned set and its order are
  fully deterministic:

  ```
  graph-global retrieved_n = 1138
  hit_rate=1.000 recall=0.972 MRR=0.0162 leakage=0.0098 median_n=1138
    {'arm':'graph-global','klass':'factoid'}  hit=1.00 recall=0.97 mrr=0.0096
    {'arm':'graph-global','klass':'global'}   hit=1.00 recall=0.94 mrr=0.0432
    {'arm':'graph-global','klass':'multi-hop'}hit=1.00 recall=0.98 mrr=0.0103
  ```

  matching the docs' "recall of 1.00 and hit rate of 100%; MRR of 0.02". A metric an
  offline script can predict exactly without running the arm is not measuring the arm.
  Nothing in `metrics.py`, the results JSON, or the CLI table records that this arm has no
  rank order, so a reader comparing `graph-global 0.02` against `vector 0.34` has no way to
  know they are not the same kind of number. `Scored.retrieved_n` was added to guard exactly
  this class of mistake for hit rate and recall; MRR got no equivalent guard.

### F2 — reported leakage understates real contamination by ~37%: `source_file` is one optional string [severity: high]

- Where: `groundly/eval/gold.py:70` (guard compares against a single `source_file`),
  `gold.py:153` (`source[q.id] = ... if q.source_file else set()`); published as "measured at
  10% for the vector arm" in `docs/architecture/retrieval.md` and decision 27.
- Failure scenario: three independent leaks in the one number the CLI calls "the number that
  decides whether any of the above can be believed":
  1. **16 of 48 apd rows carry `source_file: null`** (all 8 RO cross-lingual rows, which are
     translations of `Examen.md` questions, plus 8 hand-written). For those the guard is off
     *and* `source` is `set()`, so `leakage` is a structural `0.0` folded into the mean — not
     "no leakage observed", but "not measured".
  2. **A question can sit in more than one indexed file.** apd-006's question appears
     verbatim in both `Examen.md` (chunk 654) and `Quiz 2 - OpenMP.pdf` (chunk 646); the gold
     row names only `Examen.md`. The vector arm returned *both*, at ranks 1 and 2. Reported
     leakage 0.125; actual 0.250.
  3. **A `source_file` that resolves to nothing is silent.** `resolve()` warns for an
     unresolvable `expected` but never for an unresolvable `source_file` — a typo
     (`Examen.MD`, a path prefix, a renamed file) yields `by_file.get(...) → set()` and a
     confident 0% leakage with no warning anywhere.
- Evidence: full 48-question vector run reproduced, then rescored against all four indexed
  exam/quiz files:

  ```
  reported leakage (per gold source_file): 0.0990   <- the docs' "10%"
  leakage against ALL exam/quiz files:     0.1354
  rows reported at exactly 0.0 leakage: 17
  of those, rows that DID retrieve an exam/quiz chunk: 6
  apd-006 reported 0.125, true 0.250
  ```

  The load-bearing guard also has an exact-string hole (`item["file"] == source_file`):
  `"examen.md"` passes it. That one self-neutralises into F3 rather than into contamination,
  because the mislabelled file then resolves to zero chunks.

### F3 — a gold label that no longer resolves is scored as a miss, inside `n` [severity: medium]

- Where: `groundly/eval/gold.py:124-155` (`resolve` returns `set()` + a warning) →
  `metrics.py:12,18,27` (`hit` false, `recall` 0.0, `reciprocal_rank` 0.0) →
  `metrics.py:135` (`aggregate` counts the row in `n`, not in `errors`).
- Failure scenario: `resolve`'s docstring promises "a partly-stale gold set should still
  produce numbers for the rows that are fine, with the bad rows called out". The bad rows are
  not held out — they are scored as perfect misses. Re-index apd after re-OCRing one PDF (page
  numbers shift by one, or the filename changes) and every question labelled on it silently
  becomes hit=False / recall=0.0 / RR=0.0 for **every arm**, depressing the whole table
  uniformly so no arm looks anomalous. The only signal is a `warnings` list printed above the
  table and stored in the JSON; nothing in `by_arm`/`by_arm_class` separates "arm missed" from
  "label is stale". The design already has the right mechanism — errored questions are
  excluded from quality metrics precisely so an outage cannot read as bad retrieval — and it
  is not applied here.
- Evidence:
  ```
  stale label row: hit False recall 0.0 rr 0.0
  aggregate over 1 good + 1 unresolvable: n=2 errors=0 hit_rate=0.5 recall=0.5
  ```
  apd currently has zero warnings, so this is latent today and fires on the next re-index.

### F4 — `_median` is not the median [severity: medium]

- Where: `groundly/eval/metrics.py:131-132` —
  `int(sorted(values)[len(values) // 2]) if values else 0`.
- Failure scenario: for even-length input this returns the **upper** middle element. With 48
  questions (even), `median_latency_ms` is the 25th smallest, and `median_retrieved_n` likewise.
  For graph arms whose latency distribution is wide (a cold parquet load and a cache-warm query
  differ by tens of seconds) that is a materially different number from the median, published
  in the CLI's "Median ms" column and as "~165 s per graph query" in `retrieval.md`. Not
  covered by any test: `test_aggregate_averages_across_questions` uses two rows both at
  latency 10, and `test_aggregate_reports_the_median_retrieved_set_size` uses three rows, so
  odd-length only.
- Evidence: `_median([1, 100]) == 100` (true median 50.5); `_median([1,2,3,4]) == 3` (true 2.5).
  `statistics.median` is already imported from — `mean` comes from the same module.

### F5 — a fully-errored arm publishes hit_rate 0.0 / recall 0.0 / mrr 0.0, and the test that claims to guard this checks something else [severity: medium]

- Where: `groundly/eval/metrics.py:143-152` (`... if ok else 0.0`);
  `tests/eval/test_eval_runner.py::test_a_total_provider_outage_scores_nothing_rather_than_zero`.
- Failure scenario: an unreachable `extraction` provider makes every graph-arm question error.
  `aggregate` returns
  `Aggregate(n=0, errors=48, hit_rate=0.0, recall=0.0, mrr=0.0, leakage=0.0, median_retrieved_n=0, ...)`.
  Anyone reading the results JSON (the CLI table is not the only consumer, and the JSON is
  what a thesis appendix would carry) sees three columns of real-looking zeros. `None` is
  already the established "no data" value here — `median_latency_ms` uses it.
- Evidence: reproduced above. The test whose name is *"scores nothing rather than zero"* and
  whose docstring says *"must be visible as errors, not as a real 0% hit rate"* asserts only
  `(agg["n"], agg["errors"]) == (0, 2)` and `results["errors"] == 2`. It never asserts anything
  about `hit_rate`, which is 0.0 — the exact outcome the docstring forbids. The test passes
  today and would still pass if `aggregate` returned 0.0 for a genuinely measured 0%.
  Downstream, `cli/eval.py:165` computes `max(sizes.values()) > 4 * min(sizes.values())` over
  `median_retrieved_n`; with a fully-errored arm `min` is 0, so the non-comparability warning
  fires and reports "vs 0 for the narrowest arm".

### F6 — `except Exception` reports programmer errors as provider outages [severity: medium]

- Where: `groundly/eval/runner.py:72`.
- Failure scenario: the handler's comment scopes it to "a single context overflow", but it
  catches everything raised anywhere under `retrieve_for_arm` — `ValueError`, `KeyError`,
  `AttributeError`, `ImportError`. A broken arm therefore produces a *successful* run: a
  results file on disk, exit code 0, `partial: false`, and `errors: N` where N is the number
  of questions. `cli/eval.py` prints only the first three distinct messages, and
  `logger.warning` reaches nothing by default (`groundly/__init__.py` attaches a
  `NullHandler`). The deliberate loud-failure precedent exists two lines below —
  `ArmDegradedError` is raised precisely because "it will hold for every remaining question,
  so there is nothing to salvage by going on". An unknown arm and a missing import have
  exactly that property and are not treated that way.
- Evidence: the CLI validates `--arms`, but `run()` is the library entry point:
  ```
  run("T", gold, store, arms=["vektor"])
  -> no exception. errors = 3, partial = False
  -> by_arm: [{'n':0,'errors':3,'hit_rate':0.0, ... ,'slice':{'arm':'vektor'}}]
  -> first error: "unknown retrieval arm 'vektor' — expected one of vector, hybrid-local, graph-global"
  ```

### F7 — the "~33 map calls / ~389k tokens / ~1,600 calls" cost figure is overstated by ~22% [severity: medium]

- Where: `groundly/eval/__init__.py` docstring, `groundly/cli/eval.py:65-71`
  ("measured ~33 on a 555-report graph"), `docs/architecture/retrieval.md`,
  `docs/groundly-spec.md` decision 27.
- Failure scenario: the figure is presented as measured and is the justification for the
  per-arm cost warning; decision 27 records it as the fix for an earlier wrong cost claim.
  Actual is 27 map batches + 1 reduce = 28 calls/question, so a 48-question sweep is ~1,344
  calls, not ~1,600, from ~310k tokens, not ~389k.
- Evidence: replayed graphrag's own `GlobalCommunityContext.build_context` on apd's artifacts
  with the exact `context_builder_params` `graphrag/query/factory.py:165-176` passes for
  global search (`use_community_summary=False`, `max_context_tokens=12000`,
  `include_community_rank=True`, `min_community_rank=0`, `normalize_community_weight=True`),
  under both plausible tokenizers:
  ```
  o200k  MAP BATCHES = 27  total tokens = 310782
  cl100k MAP BATCHES = 27  total tokens = 310072
  ```
  Independent cross-check: `community_reports.parquet` `full_content` at `level <= 2` totals
  295,614 cl100k tokens across 555 reports — 24.6 batches before per-row delimiter overhead.
  The 555-report and level<=2 halves of the claim are correct; only the token total and the
  call count are not.

### F8 — the collision fix does not reach local search, and it caps `hybrid-local`'s recall on four gold rows [severity: medium]

- Where: `groundly/retrieval/graph.py:207-214` (local search's `.iloc` join), vs the fixed
  global join at `graph.py:277-304`. The commit message says "both joins now build
  `id -> list[document_id]`"; there is a third consumer.
- Failure scenario: graphrag's own context builder dedupes text units by id —
  `mixed_context.py:79`, `self.text_units = {unit.id: unit for unit in text_units}` — keeping
  the **last** row for each colliding content hash. So for each of apd's 18 colliding ids, one
  real chunk becomes unreachable by local search: it can never appear in `sources`, and
  `retrieve_for_arm`'s `hybrid-local` therefore can never return it from the graph channel.
  This is the same content-hash collision decision 26 documented; the global path was fixed to
  emit both members, the local path still silently keeps one.
- Evidence: computed the survivor set from apd's `text_units.parquet` in DataFrame order and
  intersected with the gold labels:
  ```
  unreachable by local search: [196,197,218,236,237,325,408,478,527,528,531,635,709,710,711,712,789,889]
  gold-labelled among them: [196, 218]
    196 = "Curs 2 - POSIX Threads.pdf" p.27   (the only chunk on that page)
    218 = "Curs 3 - Synchronization.pdf" p.14 (the only chunk on that page)
  affected gold rows: apd-028 (multi-hop, 1 of 3 labels), apd-029 (multi-hop, 1 of 2),
                      apd-036 (global, 1 of 5), apd-044 (multi-hop RO, 1 of 2)
  ```
  So `hybrid-local`'s recall is capped at 0.5 on apd-029 and apd-044 and at 0.667/0.8 on
  apd-028/apd-036 by an artifact — on exactly the multi-hop slice the graph arms exist to win.
  Whatever hybrid-local's eventual number is, it will be understated.

### F9 — `leakage` mixes a deduplicated numerator with a raw-list denominator [severity: low]

- Where: `groundly/eval/metrics.py:44` —
  `len(set(retrieved) & source) / len(retrieved)`.
- Failure scenario: `retrieved` is a list and `expected`/`source` are sets, so any duplicate
  id deflates the ratio. `leakage([9,9,9,9], {9}) == 0.25` — four retrieved slots, all four
  from the question's own exam file, reported as 25% leakage. `retrieved_n` (which uses
  `len(retrieved)`) and `leakage` would then describe different set sizes.
- Evidence: reproduced above. Latent today, not wrong today: every arm currently returns unique
  ids (`rrf` fuses through a dict, `graph-global` sorts a set, local search's positional
  lookups are 1:1). It becomes a live wrong number the first time an arm returns a duplicate.

### F10 — nothing in the repo backs the published numbers [severity: low]

- Where: `.gitignore` (`results-*.json`, unanchored), `docs/groundly-spec.md` decision 27.
- Failure scenario: the results file that produced decision 27's baseline table and the
  graph-global finding is gitignored by design ("gold sets are versioned, the results they
  produce are not"). The only results file that survives on this machine is a **partial**
  8-question vector run, `evals/apd/results-20260804T121327+0000.json` with `"partial": true`
  — which is not the run the docs quote. For a thesis, the artifact behind every quoted figure
  is prose. (`write_results` also names files by timestamp only, not by `partial`, so a
  directory of results cannot be triaged without opening each one.)
- Evidence: `find` across the repo and `~/.groundly` returns exactly two results files, and
  they are byte-identical copies of the same partial run (one of them has been ingested into
  the `groundly` subject's `materials/`).

### F11 — CLI progress denominator counts gold comment lines differently from the parser [severity: low]

- Where: `groundly/cli/eval.py:75` — `not ln.startswith("#")` on the raw line, vs
  `gold.py:96-98` which does `line = line.strip()` *before* the `#` test.
- Failure scenario: an indented comment (`"  # note"`) is skipped by the loader but counted by
  the CLI, so the `[done/total]` progress label never reaches its denominator on a multi-hour
  run — the one signal a redirected run gives that it is not hung. Separately,
  `cli/eval.py:117` prints `results['questions'] * len(arm_list)` as the failure denominator,
  which after an interrupt is the full gold set rather than what was attempted.
- Evidence: read both code paths; not reproduced (cosmetic).

### F12 — `page: null` on a paginated file silently expands `expected` to the whole file [severity: low]

- Where: `groundly/eval/gold.py:143-147` — `by_file.get(want.file)` when `want.page is None`.
- Failure scenario: `Expected.page is None` is documented as "correct for markdown and source
  files, which have no pages", and the loader accepts it for any file. A row labelled
  `{"file": "Curs 3 - Synchronization.pdf"}` resolves to all 47 chunks of that file, making
  `hit` near-certain for any arm and `recall` a denominator of 47. No validation, no warning —
  it looks identical to a correct markdown label in the JSONL and in the results doc.
- Evidence: `test_resolve_matches_whole_file_when_page_is_null` asserts exactly this expansion
  as intended behaviour. apd's gold set has zero `page: null` labels today, so this is a format
  hole rather than a current defect.

## What I tried and could not break

- **The vector baseline reproduces exactly.** Re-ran all 48 apd questions through
  `eval.runner.run` with the real bge-m3 + reranker: hit 0.941/0.591/0.556 (factoid/multi-hop/global),
  recall 0.765/0.394/0.222, MRR 0.488/0.297/0.167, leakage 0.0990 — identical to
  `retrieval.md`'s "94% / 59% / 56%; 0.76 / 0.39 / 0.22; 0.49 / 0.30 / 0.17" and "10%".
  Zero resolution warnings. Those numbers are honest for what they measure.
- **"1,138 of apd's 1,193 chunks — 95% of the corpus"** verified exactly by replaying the
  entity → text-unit → document_id join offline: 1,121 text units → 1,138 chunk ids → 95.4%.
- **"555 reports at level <= 2"** verified (609 total, 555 at level ≤ 2). **"18 of 1,193 ids
  collide"** verified (1,175 unique ids, 36 duplicate rows = 18 colliding ids, each with
  exactly 2 members).
- **The `ask()` refactor is behaviour-preserving.** Diffed against `586a219:groundly/agents/ask.py`:
  `_LABEL_TO_ARM` reproduces the old `multi-hop`/`global`/else dispatch including the
  `router_label is None` case (`.get(None, "vector")` == the old `else` branch), both
  `GraphNotBuiltError` degradation paths still return `arm="vector"` after a full vector
  retrieve, and the trace still records `router_label` from `classify` and `arm` from what
  actually ran. The `requested_arm` shadowing is safe: `arm` is reset to `None` before the
  `try` and is only reassigned from `retrieve_for_arm`'s third return value, so an exception
  anywhere still traces `arm=NULL` exactly as before.
- **`KeyboardInterrupt` handling is correct.** The `break` exits the arm loop, control returns
  to the outer `for question`, and `if interrupted: break` at its top fires before the next
  question's arms run. No further question is attempted.
- **`zip(..., strict=True)` cannot raise on real data** — both arguments are columns of the
  same DataFrame, so the lengths are equal by construction. (A renamed `document_id` column
  would raise `KeyError` before the zip, not `ValueError` from it.)
- **Local search's positional `.iloc` lookup is right, for the reason the docstring gives.**
  Traced `sources["id"]` → `unit.short_id` (`source_context.py:56`) → `read_indexer_text_units`
  → `read_text_units(short_id_col="human_readable_id")`, and confirmed on apd's parquet that
  `human_readable_id` equals the positional index for all 1,193 rows. (Its real weakness is
  F8, not the index arithmetic.)
- **The contamination guard held for this gold set.** No apd row's `expected` names an
  exam/quiz/summary file — all 80 labels point at `Curs*` / `Overview*` lecture PDFs. Duplicate
  question ids, empty `expected`, missing `file`, bad `lang`/`class`, malformed JSON and a
  missing file all fail loudly with the offending line named.
- **Duplicate ids in `retrieved` are unreachable today** — `rrf` accumulates into a dict,
  `graph-global` sorts a set, and local search's positional lookups are 1:1 with `document_id`.
  F9 stays latent.
- **Module boundaries hold.** `groundly/eval/` imports `agents`/`core` only; nothing in
  `groundly/` imports `groundly.eval` except `cli/eval.py` (a sibling client), and that import
  is function-local.
- **Full suite: 568 passed, 24 deselected.**
