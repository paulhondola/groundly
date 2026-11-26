# Thesis Measurement Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every bench run leaves its raw facts (per page, file, query, item, and a memory/CPU/GPU/power time series) in one DuckDB store, from which R1–R15 export as Parquet and CSV.

**Architecture:** A sampler pair (psutil + libproc + ioreg at 10 Hz; `sudo -n powermetrics` at 2 Hz, one per bench invocation) wraps each measured child through `bench/common/monitor.py`. The parent buffers everything and writes one DuckDB transaction per run. A new embed bench measures torch vs MLX; a report step turns the store into one export per result.

**Tech Stack:** Python 3.14, DuckDB, psutil, ctypes/libproc, `/usr/bin/powermetrics`, `ioreg`, memray, matplotlib, pytest.

**Spec:** `docs/superpowers/specs/2026-11-26-thesis-measurement-harness-design.md`

**Precondition:** Task 9 of `docs/superpowers/plans/2026-11-26-ocr-bench-readers.md` is done (bench on `main`, `bench` dependency group). Execute this plan on a feature branch cut from `main` after that merge.

## Global Constraints

- Bench-only dependencies stay in the `bench` dependency group, pinned exactly: `duckdb`, `matplotlib` join `mineru-vl-utils[mlx]==2.0.9`; `psutil` stays where it is. Pick each at the newest release with a cp314 macOS-arm64 wheel; ledger the version.
- `bench/results/` stays gitignored; the store is `bench/results/bench.duckdb`.
- Only the parent bench process opens the store, once per run, at the end of the run. Children never import `duckdb`.
- The parent never imports torch, FlagEmbedding or MLX; engines load only in the measured child.
- powermetrics runs as `sudo -n /usr/bin/powermetrics …`; if `sudo -n true` fails, the bench exits before any run with exactly: `powermetrics needs sudo — run "sudo -v" first, or pass --no-power`. The harness never edits sudoers and never reads a password.
- A sampler that cannot read a value writes NULL and sets `run.sampler_note`; never 0.
- memray runs set `run.memray = true`; no timing query or export includes them.
- `page_text` is the only table with course text; no export selects it or joins it.
- Store raw facts only; energy, CPU %, GPU %, medians and per-cell scores are queries.
- Tests that need the bench group start with `pytest.importorskip("duckdb")` (CI syncs `--extra dev` only); tests needing sudo, MLX or real models are `@pytest.mark.slow`.
- Comments state intent and invariants; no history in code.

## Review Focus

- powermetrics dies or its sudo lapses mid-session: the runs still write, power columns NULL, `sampler_note` names the cause — pinned in Task 5.
- A child that exits before the first sample (sub-100 ms): a run row with zero `sample_proc` rows, no crash, no division by zero in later queries — pinned in Task 5.
- `import-jsonl` run twice: no duplicate sessions or runs — pinned in Task 2.
- A second bench invocation while one holds the store: a one-line error naming the lock, not a DuckDB traceback — pinned in Task 1.
- An export carrying course text: every export's columns checked against `page_text`'s — pinned in Task 8.

---

### Task 1: The store

**Files:**
- Create: `bench/common/store.py`
- Modify: `pyproject.toml` (`duckdb` into the `bench` group), `uv.lock`
- Test: `tests/bench/test_store.py`

**Interfaces:**
- Produces:
  - `SCHEMA_VERSION: int = 1`; `STORE: Path = RESULTS_DIR / "bench.duckdb"`
  - `connect(path: Path | None = None) -> duckdb.DuckDBPyConnection` — `None` means the module's `STORE` read at call time (tests repoint it); creates the schema of the spec's Store section plus `meta(schema_version)`; refuses a newer version with `bench.duckdb is schema vN, this bench knows v1`; on a lock conflict raises `StoreBusy("bench.duckdb is in use by another bench run")`.
  - `TABLES: dict[str, tuple[str, ...]]` — column names per child table (`page`, `page_text`, `file`, `query`, `query_hit`, `agreement`, `sample_proc`, `sample_power`, `sample_task`, `artifact`).
  - `ensure_session(con, session: dict) -> None` — inserts `session` and `session_package` rows once per id.
  - `write_run(con, run: dict, children: dict[str, list[dict]]) -> int` — one transaction: inserts the `run` row, fills each child row's `run` column with the new id, inserts; any failure rolls back everything; returns the run id. Unknown table or column raises `ValueError` before anything is written.

- [ ] **Step 1: Write the failing tests**

```python
duckdb = pytest.importorskip("duckdb")


def test_connect_creates_every_table(tmp_path):
    con = store.connect(tmp_path / "b.duckdb")
    names = {
        r[0] for r in con.execute("select table_name from information_schema.tables").fetchall()
    }
    assert {"meta", "session", "session_package", "run", *store.TABLES} <= names


def test_newer_schema_is_refused(tmp_path):
    p = tmp_path / "b.duckdb"
    store.connect(p).execute("update meta set schema_version = 99").close()
    with pytest.raises(SystemExit, match="schema v99, this bench knows v1"):
        store.connect(p)


def test_write_run_is_one_transaction(tmp_path):
    con = store.connect(tmp_path / "b.duckdb")
    store.ensure_session(con, SESSION)
    with pytest.raises(ValueError):
        store.write_run(con, RUN, {"page": [PAGE], "nope": [{}]})
    assert con.execute("select count(*) from run").fetchone() == (0,)
    rid = store.write_run(con, RUN, {"page": [PAGE], "page_text": [PAGE_TEXT]})
    assert con.execute("select run from page").fetchall() == [(rid,)]


def test_a_second_writer_gets_a_plain_error(tmp_path):
    p = tmp_path / "b.duckdb"
    store.connect(p)
    # a child process holds the file open for writing
    holder = subprocess.Popen([sys.executable, "-c", HOLD.format(p=p)])
    time.sleep(1)
    with pytest.raises(store.StoreBusy, match="in use by another bench run"):
        store.connect(p)
    holder.kill()
```

`SESSION`, `RUN`, `PAGE`, `PAGE_TEXT`, `HOLD` are module constants in the test file, one valid row each; `HOLD` opens the file with `duckdb.connect` and sleeps 30 s.

- [ ] **Step 2: Run them** — `uv run pytest tests/bench/test_store.py -q` → FAIL (`bench.common.store` missing).
- [ ] **Step 3: Implement `bench/common/store.py`** with the columns of the spec's Store section; `run.id` from a sequence; booleans as BOOLEAN, times as TIMESTAMP, `*_s`/`*_ms`/`*_mb`/`*_mw` as DOUBLE. Add `duckdb==<ver>` to the `bench` group, `uv lock`.
- [ ] **Step 4: Run them** — PASS.
- [ ] **Step 5: Commit** `feat(bench): DuckDB store — schema, one-transaction run writer`.

### Task 2: Import today's JSONL

**Files:**
- Modify: `bench/common/store.py` (add `import_jsonl`, a `__main__` with `import-jsonl`)
- Test: `tests/bench/test_store.py`

**Interfaces:**
- Consumes: Task 1.
- Produces: `import_jsonl(con, results_dir: Path) -> dict[str, int]` (rows inserted per table); CLI `uv run python -m bench.common.store import-jsonl`.

Mapping: each `extract`/`index`/`retrieval` row → one `run` (`bench`, `arm`, `session`, `run_no` = row `run`, `wall_s`, `load_s`, `returncode`, `memray`, `timeout_s`, `manifest_version`, `sampler_note = 'imported'`); index `failed` → `file` rows with `status = 'failed'`; index `per_file` → `file` rows with `status = 'ok'` unless failed; `results/pages/<session>-<arm>.jsonl` → `page` + `page_text` rows of the matching extract run, scores NULL and `scorer_version` NULL until Task 6's `rescore` fills them. Aggregate keys of old rows (`word_f1/image/ro`, …) are not imported: they are derivable.

- [ ] **Step 1: Write the failing tests** — `test_import_maps_rows_to_runs_and_files` (fixture dir with one row of each bench and one pages file → counts `{"run": 3, "file": 2, "page": 1, "page_text": 1}`) and `test_import_twice_adds_nothing` (second call returns all zeros; `select count(*) from run` unchanged).
- [ ] **Step 2: Run them** → FAIL.
- [ ] **Step 3: Implement**; a session already in `session` is skipped whole.
- [ ] **Step 4: Run them** → PASS.
- [ ] **Step 5: Commit** `feat(bench): import the JSONL results into the store`.

### Task 3: The proc sampler

**Files:**
- Create: `bench/common/sampler.py`
- Test: `tests/bench/test_sampler.py`

**Interfaces:**
- Produces:
  - `footprint_bytes(pid: int) -> int | None` — `ri_phys_footprint` from libproc `proc_pid_rusage(pid, RUSAGE_INFO_V2, …)` via ctypes; None off macOS or on any error.
  - `parse_ioreg(text: str) -> int | None` — `"In use system memory"=N` from `ioreg -r -d 1 -c IOAccelerator`; None when absent.
  - `class ProcSampler(root_pid: int, interval: float = 0.1)` — `start()`, `stop() -> list[dict]`; a daemon thread; each row `{t_s, rss_mb, footprint_mb, cpu_s, gpu_mem_mb_system}` over the root and all descendants, `t_s` from `time.perf_counter()` minus the start; a process vanishing mid-read is skipped; `note: str | None` set when footprint or ioreg is unavailable.

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_ioreg_reads_in_use_memory():
    assert sampler.parse_ioreg(IOREG_SAMPLE) == 1037664256
    assert sampler.parse_ioreg("nothing here") is None


@pytest.mark.skipif(sys.platform != "darwin", reason="libproc")
def test_footprint_of_this_process_is_positive():
    assert sampler.footprint_bytes(os.getpid()) > 0


def test_sampler_sees_a_child_allocate_and_burn_cpu():
    child = subprocess.Popen([sys.executable, "-c", ALLOC_200MB_AND_SPIN_1S])
    s = sampler.ProcSampler(child.pid)
    s.start()
    child.wait()
    rows = s.stop()
    assert max(r["rss_mb"] for r in rows) >= 200
    assert rows[-1]["cpu_s"] >= rows[0]["cpu_s"]
```

`IOREG_SAMPLE` is the `PerformanceStatistics` line captured on this Mac (the one in the spec's research: `"In use system memory"=1037664256`).

- [ ] **Step 2: Run them** → FAIL.
- [ ] **Step 3: Implement.** CPU seconds of exited descendants are lost by psutil; accept it and say so in the module docstring.
- [ ] **Step 4: Run them** → PASS.
- [ ] **Step 5: Commit** `feat(bench): process-tree sampler — RSS, footprint, CPU, GPU memory`.

### Task 4: The power sampler

**Files:**
- Modify: `bench/common/sampler.py`
- Create: `tests/bench/fixtures/powermetrics.plist`
- Test: `tests/bench/test_sampler.py`

**Interfaces:**
- Produces:
  - `sudo_ok() -> bool` — `sudo -n true` returns 0.
  - `parse_plists(data: bytes) -> list[dict]` — powermetrics' `-f plist` stream: NUL-separated plist documents, a trailing partial one dropped.
  - `power_rows(samples: list[dict], t0: float, pids: set[int]) -> tuple[list[dict], list[dict]]` — `sample_power` rows `{t_s, cpu_mw, gpu_mw, ane_mw, combined_mw, gpu_active, gpu_freq_mhz}` and `sample_task` rows `{t_s, pid, name, cpu_ms_per_s, gpu_ms_per_s, energy_impact}` for `pids` only; `t_s` relative to `t0`.
  - `class PowerSession` — context manager for one bench invocation: starts `sudo -n /usr/bin/powermetrics --samplers cpu_power,gpu_power,ane_power,tasks --show-process-gpu --show-process-energy -i 500 -f plist -o <tmp>`; `idle_mw(seconds: float = 10) -> float | None` (mean `combined_mw` over a quiet window, measured at entry); `slice(t_start: float, t_end: float, pids: set[int]) -> tuple[list[dict], list[dict]]`; `alive() -> bool`; `note: str | None` (why rows are missing). Exit terminates powermetrics with `sudo -n kill` and deletes the temp file.

- [ ] **Step 1: Capture the fixture — STOP for Paul.** Ask Paul to run once in his own terminal (sudo prompts there, never in the harness):

```bash
sudo powermetrics --samplers cpu_power,gpu_power,ane_power,tasks --show-process-gpu --show-process-energy -i 500 -n 2 -f plist -o tests/bench/fixtures/powermetrics.plist
```

Expected: a file of two NUL-separated plists. Read its keys; the plan's row fields map to whatever keys this macOS writes (ledger the mapping). Process names in it are not course data; keep it.

- [ ] **Step 2: Write the failing tests** — `test_parse_plists_reads_both_samples` (2 dicts from the fixture; a truncated copy gives 1), `test_power_rows_keeps_only_the_tree` (task rows only for a pid present in the fixture; system rows have `combined_mw > 0`), `test_power_session_refuses_without_sudo` (monkeypatch `sudo_ok` → False; entering raises `SystemExit` with the Global Constraints message).
- [ ] **Step 3: Run them** → FAIL.
- [ ] **Step 4: Implement.**
- [ ] **Step 5: Run them** → PASS. Then, slow, if `sudo_ok()`: `test_power_session_records_while_alive` — a 3 s session yields ≥ 4 system rows.
- [ ] **Step 6: Commit** `feat(bench): powermetrics session — power, GPU and per-task samples`.

### Task 5: Monitor with samplers

**Files:**
- Modify: `bench/common/monitor.py`
- Test: `tests/bench/test_monitor.py` (create if absent)

**Interfaces:**
- Consumes: `ProcSampler`, `PowerSession` (Tasks 3–4).
- Produces: `run(cmd, *, env=None, cwd=None, interval=0.1, memray_out=None, memray_python=None, power: PowerSession | None = None) -> Measurement`, where `Measurement` gains `started_at: datetime`, `ended_at: datetime`, `samples: dict[str, list[dict]]` (keys `sample_proc`, `sample_power`, `sample_task`) and `sampler_note: str | None`. `peak_rss_mb` stays, computed from the proc rows.

- [ ] **Step 1: Write the failing tests** — `test_measurement_carries_proc_samples` (a 1 s child → `len(samples["sample_proc"]) >= 5`, `started_at < ended_at`); `test_instant_child_writes_no_samples_and_does_not_crash` (`python -c pass` → `samples["sample_proc"] == []` or one row, `peak_rss_mb >= 0`); `test_dead_power_session_leaves_a_note` (a stub `PowerSession` whose `alive()` is False → `samples["sample_power"] == []`, `sampler_note` contains `powermetrics`).
- [ ] **Step 2: Run them** → FAIL.
- [ ] **Step 3: Implement**: start `ProcSampler` on the Popen pid; the peak loop goes; power rows from `power.slice(start, end, pids_seen)`, where `pids_seen` is every pid the proc sampler saw.
- [ ] **Step 4: Run them, then `uv run pytest tests/bench -q`** → PASS, suite green.
- [ ] **Step 5: Commit** `feat(bench): measured runs carry proc and power samples`.

### Task 6: The benches write to the store

**Files:**
- Modify: `bench/extract/score.py`, `bench/extract/run.py`, `bench/index/run.py`, `bench/retrieval/run.py`, `bench/common/compare.py`, `bench/common/results.py`, `bench/README.md`
- Test: `tests/bench/test_score.py`, `tests/bench/test_extract_run.py`, `tests/bench/test_index_run.py`, `tests/bench/test_retrieval_run.py`

**Interfaces:**
- Consumes: Tasks 1, 5.
- Produces:
  - `page_scores(manifest: dict, outputs: list[dict], reference_dir: Path) -> list[dict]` in `score.py` — one dict per scored page `{sha256, page, f1, precision, recall, bigram_f1, diacritic_recall}`, same skip rules as `score`; `SCORER_VERSION: int = 1`. `score` is rebuilt on top of it, unchanged in output.
  - `query_rows(labelled: list[dict], hits: list[list[tuple[str, int]]], latencies_ms: list[float]) -> tuple[list[dict], list[dict]]` in `retrieval/run.py` — `query` and `query_hit` rows.
  - Each bench `main` gains `--no-power` and `--runs` (defaults: extract 1, index 5, retrieval 1); opens one `PowerSession` per invocation unless `--no-power`; writes each run with `store.write_run`; no longer calls `results.append`. `results.append` is deleted; `results.machine`, `versions`, `new_session` stay.
  - Index `--memray` records two `artifact` rows per run (`process = 'parent'` from `monitor.run(memray_out=…)`, `process = 'worker'` from `worker_argv`), each with `peak_heap_mb` from `memray stats --json <bin>` and a `memray_html` artifact from `memray flamegraph -o <html> <bin>`.
  - Run rows carry `idle_combined_mw` from `power.idle_mw()` (NULL with `--no-power`), `power_sampler` (`'powermetrics'` or `'none'`), `started_at`/`ended_at` from the `Measurement`, and the `sampler_note`.
  - `rescore(con, reference_dir: Path) -> int` in `score.py` and CLI `uv run python -m bench.extract.score rescore`: recomputes `page` scores from `page_text` and the references for every row whose `scorer_version` is not `SCORER_VERSION`; returns rows updated.
  - `compare.py` reads `run` from the store (same table and gate as today), excluding `memray` runs.

- [ ] **Step 1: Write the failing tests** — `test_page_scores_match_the_aggregate` (mean of `f1` per cell equals `score`'s `word_f1/<cell>` on the existing fixtures); `test_query_rows_mark_hits_and_latency`; `test_extract_main_writes_run_and_pages` (stub stack via a tiny `bench/stacks` test module writing two page rows; `--no-power`; store in `tmp_path` via `monkeypatch.setattr(store, "STORE", …)` → one `run`, two `page`, two `page_text`); `test_compare_reads_the_store_and_skips_memray`; `test_rescore_updates_only_stale_rows` (one row at `SCORER_VERSION`, one NULL → returns 1, both now current).
- [ ] **Step 2: Run them** → FAIL.
- [ ] **Step 3: Implement**, file by file; README's metrics section names the tables instead of JSONL rows.
- [ ] **Step 4: Run `uv run pytest tests/bench -q`** → PASS.
- [ ] **Step 5: Commit** `feat(bench): extract, index and retrieval write runs to the store`.

### Task 7: The embed bench

**Files:**
- Create: `bench/embed/__init__.py`, `bench/embed/run.py`, `bench/embed/child.py`
- Test: `tests/bench/test_embed.py`

**Interfaces:**
- Consumes: Tasks 1, 5, 6 (`query_rows`).
- Produces:
  - `child.py`: `python -m bench.embed.child --engine {mlx,torch} --store <store.db> --queries <json> --subject <name> --out <npz> --facts <json>`. With `torch`, sets `groundly.llm.mlx_xlmr.mlx_available = lambda: False` and the same name in `groundly.llm.embeddings` and `groundly.llm.rerank` before any model loads. Writes to `--facts`: `{device, load_s, embed_s, rerank_s, chunks, pairs, latencies_ms, hits}`; to `--out`: dense vectors, sparse weights, rerank scores, keyed by chunk id and query id.
  - `run.py`: `agreement_rows(a: Path, b: Path) -> list[dict]` — per chunk `dense_cos`, `sparse_top10_overlap`; per query `rerank_top5_overlap`, `max_score_diff`. CLI `--subject --home --queries --runs 5 --no-power`; arms interleaved; each run through `monitor.run`; after every round, `agreement` rows for that round's mlx/torch pair; `.npz` kept as `artifact(kind='npz')` under `bench/results/embed/`.

- [ ] **Step 1: Write the failing tests** — `test_agreement_rows_on_identical_outputs_are_perfect` (two copies of a synthetic npz → `dense_cos == 1`, overlaps `== 1`, `max_score_diff == 0`); `test_agreement_rows_see_a_swap` (top-5 differs by one item → `0.8`). Slow: `test_torch_arm_really_runs_torch` (child with `--engine torch` on a 3-chunk store → `facts["device"]` is not `"mlx"`).
- [ ] **Step 2: Run them** → FAIL.
- [ ] **Step 3: Implement.** Embedding goes through `groundly.llm.embeddings`' public encoder and reranking through `groundly.llm.rerank`'s, the same calls `vector.search` makes; `search` latency is `vector.search` timed per query.
- [ ] **Step 4: Run them** → PASS; `uv run pytest tests/bench -q` green.
- [ ] **Step 5: Commit** `feat(bench): torch vs MLX embed and rerank bench`.

### Task 8: Report — R1–R15 exports and figures

**Files:**
- Create: `bench/report/__init__.py`, `bench/report/run.py`, `bench/report/queries.sql`
- Modify: `pyproject.toml` (`matplotlib` into the `bench` group), `uv.lock`
- Test: `tests/bench/test_report.py`

**Interfaces:**
- Consumes: Tasks 1–7's tables.
- Produces: `RESULTS: dict[str, str]` — `R1`…`R15` → SQL (named blocks in `queries.sql`); `export(con, out_dir: Path, session: str | None = None) -> list[Path]` — `COPY (query) TO '<out_dir>/<R>.parquet' (FORMAT parquet)` and the same `.csv`, latest session per bench when `session` is None; `bootstrap_ci(values: list[float], n: int = 2000, seed: int = 0) -> tuple[float, float]` for R1; `figures(out_dir: Path) -> list[Path]` — one PNG per R from the Parquet files. CLI `uv run python -m bench.report.run [--session S]`.

Energy per run = trapezoid integral of `combined_mw` over the run window minus `idle_combined_mw × wall_s`; CPU % = Δ`cpu_s` / Δ`t_s` × 100; every query filters `memray = false`.

- [ ] **Step 1: Write the failing tests** — `test_every_result_exports` (fixture store with one run per bench and a few samples → 15 Parquet + 15 CSV files); `test_no_export_carries_page_text` (no export has a `text` column, and none of the fixture's `page_text` strings appears in any CSV); `test_bootstrap_ci_brackets_the_mean`; `test_energy_subtracts_idle` (constant 1000 mW for 10 s, idle 400 mW → 6 J).
- [ ] **Step 2: Run them** → FAIL.
- [ ] **Step 3: Implement.** Figure styling is the builder's.
- [ ] **Step 4: Run them** → PASS; suite green.
- [ ] **Step 5: Commit** `feat(bench): report — R1–R15 as Parquet, CSV and figures`.

### Task 9: Phase-1 and phase-2 acceptance

**Files:** none (results only).

- [ ] **Step 1: Import** — `uv run python -m bench.common.store import-jsonl` → counts printed; today's session `20261126T094955Z` present.
- [ ] **Step 2: Power access — STOP for Paul.** The harness can use powermetrics only if `sudo -n true` succeeds in the shell that runs it. Ask Paul to either run the benches below himself after `sudo -v`, or add a sudoers rule for `/usr/bin/powermetrics` himself. Do not proceed with power until he says which.
- [ ] **Step 3: One session per bench on apd** — extract (`--stack docling --only-ref`), index (`--stacks docling --runs 1 --subject apd --input ~/.groundly/apd/materials`, then once with `--memray`), retrieval on its kept home, embed (`--subject apd --runs 1`).
Expected: every table the spec lists has rows from this session; no NULL power or footprint column without a `sampler_note`.
- [ ] **Step 4: Report** — `uv run python -m bench.report.run` → 15 Parquet, 15 CSV, PNGs; open R5 and R6 and check the traces look like the run.
- [ ] **Step 5: Final review** per superpowers:executing-plans, then finishing-a-development-branch.
