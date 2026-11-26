# Thesis measurement harness — design

Status: design, not built. Approved shape so far (Paul, 2026-11-26): extend the existing `bench/`, powermetrics as the power sampler, DuckDB as the store with Parquet export, results R1–R15, columnar schema of raw facts.

## Intent

The thesis compares the OCR engines Groundly could read with, and the embedding/reranking engines it runs, on **accuracy, time, memory, CPU/GPU use and energy**, and has to be re-run whenever Paul adds subjects. Today's bench answers "does a VLM stack pass the ship rule" with one aggregate row per run; the thesis needs every run's raw facts kept, comparable across sessions, and turned into figures and tables reproducibly. Paul's teacher suggested the chapter: display every OCR model's performance.

## Goals

- Every measured run leaves raw facts: per page, per file, per query, per item, and a time series of memory, CPU, GPU and power.
- One store for all sessions; each figure or table (R1–R15) is a query over it, exported as Parquet (and a CSV twin).
- Torch vs MLX for bge-m3 and the reranker measured on the same Mac, same items, same harness.
- memray profiles of both processes of an index run: the pipeline parent (embedding) and the extraction worker.
- Repeats: every bench takes `--runs`; defaults 5 for index and embed, 1 for extract (accuracy is deterministic; pass more for latency spread). *Builder's call.*
- Re-runnable by one command per bench; a run on a machine without powermetrics or MLX still works, with those columns NULL.

## Non-goals

- No product change: no engine setting, no reader flag, no metrics panel in Groundly's page (that needs a `/decision`, decisions 38/44).
- No new OCR engine and no change to the ship rule or decision 48/49.
- The static viewer (a page under `bench/` reading the Parquet exports) is out of scope for this design; the exports are shaped so it can be added later.

## Anti-goals

- Course text in any export, figure or viewer input.
- Timing numbers taken from memray runs.
- A harness that silently records zeros when a sampler fails (NULL and a recorded reason, never 0).
- The harness editing sudoers or holding a password.

## Constraints

- Bench-only dependencies (`duckdb`, `psutil`, `mineru-vl-utils[mlx]`) stay in the `bench` dependency group (Task 9 of Plan 2), pinned exactly.
- `bench/results/` stays gitignored: the store holds page text (trust layer 3).
- powermetrics needs root. The harness runs `sudo -n /usr/bin/powermetrics …` and, if sudo would prompt, stops before any run with: `powermetrics needs sudo — run "sudo -v" first, or pass --no-power`. Long chains need either a fresh `sudo -v` per invocation or a sudoers rule limited to `/usr/bin/powermetrics`; that rule is Paul's to add, never the harness's.
- Only the parent bench process opens the DuckDB file, once per run, at the end of the run (DuckDB allows one writing process). Children write nothing to it.
- Measurement code never imports torch, FlagEmbedding or MLX in the parent; engines load only in the measured child.

## Approaches (settled)

- **Extend the existing bench** — picked. Sessions, interleaved schedules, scratch homes, the stack contract and the scorers already exist; the harness adds a sampler, a store, an embed bench and a report.
- A benchmarking library (pyperf, asv, pytest-benchmark) — rejected: no process-tree memory, GPU, energy or accuracy.
- Sampler: psutil/ioreg only (no sudo) vs powermetrics — Paul chose powermetrics: energy per page and per chunk is a thesis result.
- Store: JSONL (today) vs SQLite vs DuckDB — Paul chose DuckDB: analysis in SQL over millions of samples and one-line Parquet export for figures and a possible viewer.

## The results it must produce

A — OCR engines (docling, MinerU, Granite; `docling_mineru`, `docling_granite` where the pipeline matters):
R1 word F1/precision/recall per engine × kind × language with bootstrap 95% CIs · R2 per-page F1 spread · R3 cost (s/page, J/page) vs F1 · R4 per-page latency CDF and model load · R5 peak memory incl. GPU memory, and one memory-over-time trace · R6 CPU/GPU/ANE use, power, energy per page · R7 capped pages, failed files, timeouts · R8 index time per subject per arm (median, range over N runs), stage split extract/embed/other, time vs subject size · R9 hit@8 per arm per subject.

B — Embedding/reranking engines (torch vs MLX):
R10 throughput (chunks/s, pairs/s) and model load · R11 `search` p50/p95 · R12 memory, CPU, GPU, energy per 1,000 chunks · R13 agreement: dense cosine, sparse top-10 overlap, rerank top-5 overlap, max score difference · R14 hit@8 per engine.

C — R15 memray: peak heap and top allocators per process, memray's HTML reports as the figures.

## Architecture

```
bench/
  common/
    store.py       DuckDB schema, writer (one transaction per run), JSONL import
    sampler.py     psutil + libproc footprint + ioreg GPU memory (10 Hz); powermetrics (2 Hz)
    monitor.py     runs one measured child under the samplers; returns facts + sample buffers
    results.py     session provenance (machine, versions, sha) — kept
  extract/ index/ retrieval/   existing benches, now writing to the store
  embed/run.py     torch vs MLX for bge-m3 and the reranker
  report/run.py    R1–R15 queries → bench/results/export/<R>.parquet + .csv
```

### Sampler

One `Sampler` per measured child, started before the child and stopped after it exits; buffers rows in memory and hands them to the writer.

- **proc sampler, 10 Hz** (`sample_proc`): over the child and all descendants — sum of RSS, sum of `phys_footprint` (libproc `proc_pid_rusage`, what Activity Monitor calls Memory; includes Metal/IOKit memory, which RSS can miss for MLX), total CPU seconds (user+system) so CPU % is a difference; system-wide GPU memory in use from `ioreg -c IOAccelerator` ("In use system memory"), labelled system-wide.
- **power sampler, 2 Hz** (`sample_power`, `sample_task`): one `sudo -n powermetrics --samplers cpu_power,gpu_power,ane_power,tasks --show-process-gpu --show-process-energy -i 500 -f plist` per bench invocation, streamed to a temp file and parsed afterwards; each run takes the samples inside its time window. System rows: CPU, GPU, ANE and combined power (mW), GPU active ratio and frequency. Task rows: per PID, CPU ms/s, GPU ms/s, energy impact — filtered to the child's process tree, which is what attributes GPU time to the worker.
- **idle baseline**: at the start of each bench invocation (a session can span several, as extract does per engine), 10 s of power samples with nothing measured; the mean is copied to every run of that invocation (`run.idle_combined_mw`), so figures can report energy above idle. *Builder's call: per invocation, not per session, because the machine's idle draw drifts over hours.*
- `--no-power` skips powermetrics; off macOS the proc sampler drops footprint and GPU memory. Missing values are NULL with `run.sampler_note` saying why.

Energy, CPU % and GPU % are computed in queries from the samples, never stored as run columns.

### memray

`--memray` traces both processes of an index run: the parent under `memray run --follow-fork`, the worker under its own `memray run` (an exec'd process is not followed). Each capture is an `artifact` row with its peak heap from `memray stats --json`; `memray flamegraph` HTML is written beside it. memray runs carry `run.memray = true`, and every timing query excludes them.

### Embed bench (`bench/embed/run.py`)

- Workload: the chunks of one subject's `store.db` (a kept home or `~/.groundly/<subject>`) and that subject's labelled queries (`bench/retrieval/queries/<subject>.json`).
- Arms `mlx` and `torch`, each run in a fresh child, interleaved, N runs (default 5). The torch arm forces FlagEmbedding by making `groundly.llm.mlx_xlmr.mlx_available` return False inside the child before any model loads — a bench-side patch, no product setting. The child records the device torch chose.
- Per run: model load seconds, embedding wall time over all chunks, rerank wall time over each query's candidate pool, `search` latency per query, hits per query.
- Outputs (dense vectors, sparse weights, rerank scores) go to an `.npz` artifact per run; after each interleaved round the parent compares the two arms item by item and writes `agreement` rows (decision 48's measures).

### Store (DuckDB, `bench/results/bench.duckdb`)

Raw facts only; every aggregate is a query. Columns sketched; the builder fixes types.

- `session(id, started_at, machine, os, git_sha, note)` · `session_package(session, package, version)`
- `run(id, session, bench[extract|index|retrieval|embed], arm, subject, run_no, started_at, ended_at, returncode, wall_s, load_s, memray, timeout_s, manifest_version, device, items, idle_combined_mw, power_sampler, sampler_note)`
- `page(run, sha256, page, kind, lang, ref, ms, capped, error, f1, precision, recall, bigram_f1, diacritic_recall, scorer_version)` · `page_text(run, sha256, page, text)` — the only table with course text; never exported
- `file(run, filename, pages, extract_s, embed_s, status, error)`
- `query(run, query_id, latency_ms, hit_within1_at8)` · `query_hit(run, query_id, rank, filename, page, score)`
- `agreement(run_a, run_b, item_kind[chunk|query], item_id, dense_cos, sparse_top10_overlap, rerank_top5_overlap, max_score_diff)`
- `sample_proc(run, t_s, rss_mb, footprint_mb, cpu_s, gpu_mem_mb_system)` · `sample_power(run, t_s, cpu_mw, gpu_mw, ane_mw, combined_mw, gpu_active, gpu_freq_mhz)` · `sample_task(run, t_s, pid, name, cpu_ms_per_s, gpu_ms_per_s, energy_impact)`
- `artifact(run, kind[memray|memray_html|npz], path, process[parent|worker|child], peak_heap_mb)`

Page scores are stored with `scorer_version` because tokenizing is Python, not SQL; `bench rescore` recomputes them from `page_text` and the references when the scorer changes. Schema version in a one-row `meta` table, checked on open; a newer version is refused.

**Migration**: `bench.common.store import-jsonl` loads today's `extract.jsonl`, `index.jsonl`, `retrieval.jsonl` and `results/pages/*.jsonl` once (runs without samples, `sampler_note = 'imported'`); the JSONL files are then no longer written.

### Report (`bench/report/run.py`)

One SQL query per result R1–R15 (bootstrap CIs for R1 in Python over the page rows), each written with `COPY … TO` as `bench/results/export/<R>.parquet` and `.csv`. Exports select numeric and status columns only; `page_text` is never joined. Figures are drawn from the exports (matplotlib, bench group); drawing style is left to the builder.

## Phases

0. **Task 9 of Plan 2** — the bench lives on `main` with a `bench` dependency group. Everything below is built there.
1. **Harness core** — store + writer + JSONL import; sampler (proc, power, idle baseline); monitor wiring for extract, index, retrieval; memray on both processes; the embed bench. Done when one session of each bench, on one subject, fills every table it should and no column a sampler could read is NULL without a note.
2. **Results** — report queries R1–R15, Parquet/CSV exports, figures. Done when every R has an export from the phase-1 session.

The full benchmark (more subjects, apd, pm, the timed-out files) runs after phase 2, when Paul has added subjects.

## Left to the builder

- powermetrics plist key names on this macOS version, and the NUL-separated sample framing.
- The libproc call for `phys_footprint` (ctypes) and its fallback when it fails.
- DuckDB column types, primary keys, and how runs get ids.
- How the parent buffers samples (in memory is expected: ~6,000 proc rows per 10-minute run).
- Figure styling.
