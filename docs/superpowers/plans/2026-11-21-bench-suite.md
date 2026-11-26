# Bench Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A repo-level `bench/` with three bench types (extract, index, retrieval) and per-stack scripts, then use it to settle decision 49's speed gate and record the first baseline.

**Architecture:** `bench/common/` holds the process-tree monitor, result rows and the comparison table. A stack is one module in `bench/stacks/` with a worker mode (plugged into Groundly's real `IngestionPipeline` through `SubprocessExtractor(argv=…)`) and a page mode (per-page text for accuracy). The index bench alternates stacks run by run in one session, from one checkout; `docling_standard` is what `main` does.

**Tech Stack:** Python 3.11, psutil 7.2, memray 1.20, pypdfium2 5.14, docling 2.115 (via Groundly's worker functions), mineru-vl-utils[mlx] 2.0.9 (optional `bench` extra), pytest, uv.

**Spec:** `docs/superpowers/specs/2026-11-21-bench-suite-design.md`

## Global Constraints

- Work on branch `bench-suite`, never `main`. Commit after each task.
- `bench/` is versioned; `bench/results/` and `bench/reference/` are gitignored. Result rows and the manifest carry hashes, page numbers and scores, **never page text**.
- `groundly/` never imports `bench`, and product code is not changed for the bench: stacks reach the pipeline only through the existing `SubprocessExtractor(argv=…)` and `serve_stdio(handle=…)` seams.
- Stacks run as `python -m bench.stacks.<name>` with `PYTHONPATH` = repo root (the worker starts in a temp dir).
- No bench dependency in Groundly's default install. MinerU goes in an optional `bench` extra only, and the lock may **add** packages but never change an existing one (docling, rapidocr, onnxruntime, opencv-python-headless 5.0, torch, transformers, mlx 0.32.4).
- OCR language: RapidOCR defaults (`ocr_lang=None`); every row records `ocr_lang`.
- Gate input: the 187 files in `~/.groundly/apd/materials/`; default `config.toml`; each scratch home symlinks `models/` → `~/.groundly/models`.
- Proposed gate: `docling_routed` median wall ≤ **0.85 ×** `docling_standard` median, same session (pipeline time, not CLI time).
- Census thresholds come from `groundly/ingestion/pdf_router.py` (`classify_page`, `is_slide_deck`), no new constants.
- Tests live in `tests/bench/`, run in the fast suite (`uv run pytest tests/bench`), no model downloads.

## Review Focus

1. A child of the monitored process exits between samples: the monitor keeps sampling the rest and never crashes (Task 1 test).
2. A manifest file is missing or its sha256 changed: loading fails loudly, naming the file, before any stack runs (Task 3 test).
3. A stack crashes or skips a page: the page counts as a failure in the row, it is not silently dropped from the averages (Task 5 test).
4. An index run exits non-zero: its row is kept with the return code, left out of medians, and `compare` says how many were dropped (Task 2 test).
5. Rows from different sessions, machines or manifest versions: `compare` never puts them in one table; it uses the latest session that holds the baseline unless told otherwise (Task 2 test).

---

### Task 1: Scaffold, process-tree monitor, convention amendment — DONE (0294f09..333460d)

**Files:**
- Create: `bench/__init__.py`, `bench/common/__init__.py`, `bench/common/monitor.py`, `tests/bench/__init__.py`, `tests/bench/test_monitor.py`
- Modify: `.gitignore` (add `bench/results/`, `bench/reference/`); the "measurement protocols and results are kept outside the repo" line in `.claude/rules/conventions.md:7`, `CLAUDE.md:12`, `docs/groundly-spec.md:8`

**Interfaces:**
- Produces: `Measurement` (frozen dataclass: `wall_s: float`, `peak_rss_mb: float`, `returncode: int`); `run(cmd: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None, interval: float = 0.1, memray_out: Path | None = None) -> Measurement`

- [ ] **Step 1: Write the failing tests**

```python
def test_peak_sums_the_process_tree(tmp_path):
    # parent allocates 150 MB, then starts a child that allocates 150 MB and both sleep 1 s
    m = run([sys.executable, "-c", PARENT_AND_CHILD_150MB])
    assert m.returncode == 0
    assert m.peak_rss_mb >= 280


def test_short_lived_children_do_not_crash_the_sampler():
    # parent spawns 50 children that exit immediately, one every 20 ms
    m = run([sys.executable, "-c", SPAWN_50_SHORT_CHILDREN], interval=0.01)
    assert m.returncode == 0


def test_nonzero_exit_is_reported():
    assert run([sys.executable, "-c", "raise SystemExit(3)"]).returncode == 3
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/bench/test_monitor.py -v`
Expected: FAIL, `ModuleNotFoundError: bench.common.monitor`

- [ ] **Step 3: Implement `run` in `bench/common/monitor.py`**

Start the command with `subprocess.Popen`. Every `interval`, sum `memory_info().rss` over `psutil.Process(pid)` plus `children(recursive=True)`, skipping `psutil.NoSuchProcess`/`AccessDenied` per process, and keep the max. When `memray_out` is set, prefix `[sys.executable, "-m", "memray", "run", "--follow-fork", "-o", str(memray_out)]`. The docstring says memray follows forks only, so the `exec`'d extraction worker is not traced.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/bench/test_monitor.py -v`
Expected: 3 passed

- [ ] **Step 5: Amend the convention line in all three files to say:** "Bench code and its page manifest live in `bench/`; results and reference text stay out of git (`bench/results/`, `bench/reference/`, gitignored). Protocols and results of the retired experiments are kept outside the repo; their code is at tag `thesis-experiments-2026-09`."

- [ ] **Step 6: Commit**

```bash
git add bench tests/bench .gitignore .claude/rules/conventions.md CLAUDE.md docs/groundly-spec.md
git commit -m "feat(bench): scaffold bench/ and process-tree monitor"
```

---

### Task 2: Result rows and the comparison table

**Files:**
- Create: `bench/common/results.py`, `bench/common/compare.py`, `tests/bench/test_results_compare.py`

**Interfaces:**
- Consumes: `Measurement` (Task 1)
- Produces:
  - `new_session() -> str` (UTC timestamp `YYYYmmddTHHMMSSZ`)
  - `machine() -> str` (`sysctl -n hw.model`, RAM GB, macOS version, e.g. `"MacBookPro18,1 16GB macOS-27.2"`)
  - `versions(python: Path) -> dict[str, str]`: runs `python -c` with `importlib.metadata` for `docling, rapidocr, pypdfium2, mlx, onnxruntime, torch`, missing packages omitted
  - `append(bench: str, row: dict, results_dir: Path = RESULTS_DIR) -> None`: writes one JSON line to `results_dir / f"{bench}.jsonl"`; `RESULTS_DIR = Path(__file__).parents[1] / "results"`
  - A row has these keys: `bench, session, arm, sha, machine, versions, ocr_lang, manifest_version (or None), run, returncode, metrics: dict[str, float]`
  - `compare.py` CLI: `python -m bench.common.compare <bench> --baseline <arm> [--metric wall_s] [--session S] [--gate RATIO]`
  - `table(rows: list[dict], baseline: str, metric: str, gate: float | None) -> str`

- [ ] **Step 1: Write the failing tests**

```python
def test_append_writes_one_line_per_row(tmp_path):
    append("index", ROW, results_dir=tmp_path)
    append("index", ROW, results_dir=tmp_path)
    assert len((tmp_path / "index.jsonl").read_text().splitlines()) == 2


def test_table_reports_median_spread_and_ratio():
    rows = rows_for("main", [240, 236, 238]) + rows_for("router", [200, 204, 198])
    out = table(rows, baseline="main", metric="wall_s", gate=0.85)
    assert "238.0" in out and "200.0" in out and "0.84" in out and "PASS" in out


def test_failed_runs_are_dropped_and_counted():
    rows = rows_for("main", [240, 238]) + [row("main", 10, returncode=1)]
    assert "1 failed run dropped" in table(rows, "main", "wall_s", None)


def test_only_the_latest_session_holding_the_baseline_is_compared():
    old = rows_for("router", [150], session="S1")
    new = rows_for("main", [240]) + rows_for("router", [200])  # session "S2"
    out = table(old + new, "main", "wall_s", None)
    assert "150" not in out


def test_mixed_machines_or_manifests_refuse():
    rows = rows_for("main", [240]) + rows_for("router", [200], machine="Other")
    with pytest.raises(ValueError, match="machine"):
        table(rows, "main", "wall_s", None)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/bench/test_results_compare.py -v`
Expected: FAIL, import error

- [ ] **Step 3: Implement `results.py` and `compare.py`**

`table` filters to `session` (default: the latest session containing `baseline`), raises `ValueError` naming the field if `machine` or `manifest_version` differ within it, drops `returncode != 0`, and prints per arm: n, median, min–max, ratio to the baseline median, plus `PASS`/`FAIL` against `gate` for non-baseline arms. Use the `statistics` module.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/bench/test_results_compare.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add bench/common tests/bench
git commit -m "feat(bench): result rows and same-session comparison table"
```

---

### Task 3: Census, language tag, stratified sample, manifest

**Files:**
- Create: `bench/common/corpus.py`, `bench/extract/__init__.py`, `bench/extract/sample.py`, `tests/bench/test_corpus.py`

**Interfaces:**
- Produces:
  - `page_lang(text: str) -> Literal["ro", "en", "?"]`: count Romanian stopwords (`și şi în de la cu pentru este care sau din pe un o ale sunt mai`) against English (`the of and to is in for that with are be this on as`) over lowercased word tokens; any of `ăâîșțşţ` counts +3 for RO; "?" when both counts are 0
  - `census(pdfs: list[tuple[str, Path]]) -> list[dict]`: one row per page `{subject, sha256, path_hint, page, kind, lang, deck}`; `kind` from `pdf_router.classify_page`, `deck` from `pdf_router.is_slide_deck`; a page with lang "?" takes its document's majority of known pages, else "?"
  - `sample(rows: list[dict], n_total: int = 200, n_ref: int = 40, seed: int = 0) -> list[dict]`: adds `ref: bool`. The ref pages are only `kind in ("mixed", "image")`, split 20 RO / 20 EN where possible. The rest is spread evenly over the (kind, lang, deck) strata present, with `random.Random(seed)`.
  - `write_manifest(pages: list[dict], path: Path) -> None`; `load_manifest(path: Path) -> dict` → `{"version": int, "created": str, "pages": [...]}`. Loading re-hashes every distinct file and raises `FileNotFoundError`/`ValueError` naming `path_hint` on a missing file or a sha mismatch.
  - CLI: `python -m bench.extract.sample [--home ~/.groundly] [--out bench/corpus/manifest.json] [--version N]`: census over every `*.pdf` in `<home>/*/materials/`, prints per-subject page counts by kind × lang, writes the manifest

- [ ] **Step 1: Write the failing tests**

```python
def test_page_lang():
    assert page_lang("Algoritmul este folosit pentru sortare și căutare") == "ro"
    assert page_lang("Algoritmul este folosit pentru sortare") == "ro"  # no diacritics
    assert page_lang("The algorithm is used for sorting and search") == "en"
    assert page_lang("x = 3; y = 4;") == "?"


def test_sample_is_deterministic_and_refs_are_ocr_pages():
    rows = synthetic_census()  # every (kind, lang, deck) combination, 50 pages each
    a, b = sample(rows, seed=0), sample(rows, seed=0)
    assert a == b and len(a) == 200
    refs = [p for p in a if p["ref"]]
    assert len(refs) == 40 and {p["kind"] for p in refs} <= {"mixed", "image"}
    assert sum(p["lang"] == "ro" for p in refs) == 20


def test_load_manifest_fails_loudly_on_changed_file(tmp_path):
    pdf = make_pdf(tmp_path / "a.pdf")  # one-page pypdfium2-written PDF
    write_manifest(census([("s", pdf)]), tmp_path / "m.json")
    pdf.write_bytes(pdf.read_bytes() + b"%")
    with pytest.raises(ValueError, match="a.pdf"):
        load_manifest(tmp_path / "m.json")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/bench/test_corpus.py -v`
Expected: FAIL, import error

- [ ] **Step 3: Implement `corpus.py` and `sample.py`** to the interfaces above.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/bench/test_corpus.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add bench tests/bench
git commit -m "feat(bench): page census, language tag, stratified manifest"
```

---

### Task 4: Accuracy scorer

**Files:**
- Create: `bench/extract/score.py`, `tests/bench/test_score.py`

**Interfaces:**
- Produces:
  - `words(text: str) -> Counter[str]`: NFC, drop Markdown markup (`#`, `*`, `|`, backticks, `<!-- image -->`, link syntax `[x](y)` → `x`), lowercase, `\w+` tokens longer than 2 characters
  - `word_f1(ref: str, hyp: str) -> float` (multiset); `bigram_f1(ref: str, hyp: str) -> float`
  - `diacritic_recall(ref: str, hyp: str, *, fold: bool) -> float | None`: over reference words containing `ăâîșțşţ`. With `fold=True`, `ş→ș` and `ţ→ț` on both sides before matching. None if the reference has none.
  - `reference_text(page: dict, reference_dir: Path) -> str | None`: `ref` pages read `reference_dir / f"{sha256}-{page}.txt"` (None if not yet checked); other pages read the pypdfium2 text layer (`get_text_range()`)
  - `score(manifest: dict, outputs: list[dict], reference_dir: Path) -> dict[str, float]`: keys `f"{metric}/{kind}/{lang}"` for metric in `word_f1, bigram_f1, diacritic_recall, diacritic_recall_folded`, plus `failed_pages` and `scored_pages`. A manifest page missing from `outputs`, or with `error` set, counts in `failed_pages` and scores 0 F1.

- [ ] **Step 1: Write the failing tests**

```python
def test_markup_is_not_words():
    assert words("## Titlu\n<!-- image -->\n[link](http://x)") == Counter({"titlu": 1, "link": 1})


def test_word_f1():
    assert word_f1("alpha beta gamma", "alpha beta gamma") == 1.0
    assert word_f1("alpha beta gamma delta", "alpha beta") == pytest.approx(2 / 3)


def test_cedilla_shows_up_unless_folded():
    assert diacritic_recall("știință", "ştiinţă", fold=False) == 0.0
    assert diacritic_recall("știință", "ştiinţă", fold=True) == 1.0
    assert diacritic_recall("plain", "plain", fold=False) is None


def test_missing_page_counts_as_failed(tmp_path):
    m = manifest_with_pages(tmp_path, 2)  # two text pages of a tiny generated PDF
    out = score(m, [output_for(m["pages"][0])], reference_dir=tmp_path)
    assert out["failed_pages"] == 1 and out["scored_pages"] == 2
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/bench/test_score.py -v`
Expected: FAIL, import error

- [ ] **Step 3: Implement `score.py`** to the interfaces above (`unicodedata.normalize`, `collections.Counter`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/bench/test_score.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add bench/extract tests/bench
git commit -m "feat(bench): word, bigram and diacritic scorer"
```

---

### Task 5: Stack modules (shared worker, docling standard and routed) and the extract runner

**Files:**
- Create: `bench/stacks/__init__.py`, `bench/stacks/_worker.py`, `bench/stacks/docling_standard.py`, `bench/stacks/docling_routed.py`, `bench/extract/run.py`, `tests/bench/test_stacks.py`, `tests/bench/test_extract_run.py`

**Interfaces:**
- Consumes: `run`/`Measurement` (Task 1), `append`/`new_session`/`machine`/`versions` (Task 2), `load_manifest` (Task 3), `score` (Task 4)
- Produces:
  - A stack module defines `SUFFIXES: frozenset[str]` (default `frozenset({".pdf"})`), `extract(path: Path, ocr_lang: str | None) -> dict` (`{"pages": int | None, "chunks": [...]}`, chunks from `extract_worker.chunk_document`), and `page_texts(path: Path, pages: list[int], ocr_lang: str | None) -> dict[int, str]`. It ends with `if __name__ == "__main__": main(sys.modules[__name__])`.
  - `_worker.handler(stack) -> Callable[[Path, Path, str | None], None]`: files whose suffix is in `stack.SUFFIXES` go to `stack.extract`, everything else to `extract_worker._handle` unchanged. When the stack's result has no chunk with non-blank text, it `sys.exit(extract_worker.EXIT_NO_TEXT)`, the same rule `_handle` applies. Otherwise it writes `json.dumps(result)` to the out path.
  - `_worker.main(stack) -> None`: `--serve` → `extract_worker.serve_stdio(handler(stack))`. `--pages MANIFEST --out OUT [--only-ref] [--ocr-lang L]` → page mode: per manifest file, `stack.page_texts(...)` for its pages, one line per page `{"sha256", "page", "text", "ms", "error"}`, warming up untimed on the first file. A per-file exception writes `error` (the exception's `type: message`) on that file's pages and the run carries on.
  - `docling_standard`: `extract = extract_worker._extract_docling` (every PDF through docling whole, as on `main`). `page_texts`: `_converter(ocr_lang).convert(path).document` once, then `doc.export_to_markdown(page_no=n)`. `ms` = the conversion time ÷ the requested page count, amortized; say so in the docstring.
  - `docling_routed`: `extract = pdf_router.extract`. `page_texts`: if `not is_slide_deck`, same as standard. Otherwise walk every page in order as `pdf_router.extract` does (the `carried` title included): text/blank pages → `text_page_document(...).export_to_markdown()`, others → `_converter(ocr_lang).convert(path, page_range=(n, n)).document.export_to_markdown()`. Return only the requested pages, each timed on its own.
  - `collect(manifest: dict, out_path: Path) -> list[dict]`: reads page-mode output; manifest pages absent from it come back with `error="missing from stack output"`.
  - Runner CLI: `python -m bench.extract.run --stack NAME [--manifest bench/corpus/manifest.json] [--only-ref]`. Runs `[sys.executable, "-m", f"bench.stacks.{NAME}", "--pages", …]` under `monitor.run` with `PYTHONPATH` = repo root, then collects, scores, and appends one `extract` row with `arm=NAME`. Metrics are the score keys plus `wall_s`, `peak_rss_mb`, `ms_p50`. Page text goes to a `tempfile` that is deleted afterwards.

- [ ] **Step 1: Write the failing tests**

```python
# tests/bench/test_stacks.py — a fake stack module built with types.ModuleType
def test_claimed_suffix_goes_to_the_stack(tmp_path):
    stack = fake_stack(SUFFIXES=frozenset({".pdf"}), result={"pages": 1, "chunks": [{"text": "hi", ...}]})
    handler(stack)(touch(tmp_path / "a.pdf"), tmp_path / "out.json", None)
    assert json.loads((tmp_path / "out.json").read_text())["pages"] == 1 and stack.calls == 1

def test_other_suffixes_use_groundly_default(tmp_path):
    stack = fake_stack(SUFFIXES=frozenset({".pdf"}))
    (tmp_path / "n.txt").write_text("plain text notes long enough to chunk")
    handler(stack)(tmp_path / "n.txt", tmp_path / "out.json", None)
    assert stack.calls == 0 and json.loads((tmp_path / "out.json").read_text())["chunks"]

def test_blank_stack_output_exits_no_text(tmp_path):
    stack = fake_stack(result={"pages": 1, "chunks": [{"text": "  "}]})
    with pytest.raises(SystemExit) as e:
        handler(stack)(touch(tmp_path / "a.pdf"), tmp_path / "out.json", None)
    assert e.value.code == extract_worker.EXIT_NO_TEXT

# tests/bench/test_extract_run.py
def test_collect_marks_pages_the_stack_skipped(tmp_path):
    manifest = {"version": 1, "pages": [page("aa", 1), page("aa", 2)]}
    out = tmp_path / "o.jsonl"
    out.write_text(json.dumps({"sha256": "aa", "page": 1, "text": "x", "ms": 1.0, "error": None}) + "\n")
    assert [g["error"] for g in collect(manifest, out)] == [None, "missing from stack output"]
```

The `.txt` test needs the plain-text path of `extract_worker._handle`, which loads the bge-m3 tokenizer for chunking. If that needs a model download, mark the test `@pytest.mark.slow` and keep the other three in the fast suite.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/bench/test_stacks.py tests/bench/test_extract_run.py -v`
Expected: FAIL, import error

- [ ] **Step 3: Implement `_worker.py`, both stacks and `extract/run.py`** to the interfaces above.

- [ ] **Step 4: Run the tests, then smoke-test both modes**

Run: `uv run pytest tests/bench/test_stacks.py tests/bench/test_extract_run.py -v` → all pass (slow ones excepted).
Page mode: from a scratch manifest with 2 pages of one apd deck, `uv run python -m bench.extract.run --stack docling_routed --manifest <scratch>` → one row, `failed_pages` 0.
Worker mode: `printf '{"in": "<an apd deck>", "out": "<tmp>/o.json", "ocr_lang": null}\n' | PYTHONPATH=. uv run python -m bench.stacks.docling_routed --serve` → `{"status": "ok"}` and `o.json` has chunks.

- [ ] **Step 5: Commit**

```bash
git add bench tests/bench
git commit -m "feat(bench): stack modules with worker and page modes, extract runner"
```

---

### Task 6: MinerU stack (optional `bench` extra)

**Files:**
- Modify: `pyproject.toml` (new extra `bench = ["mineru-vl-utils[mlx]==2.0.9"]`; under `[tool.uv]`, `override-dependencies = ["opencv-python ; sys_platform == 'never'"]` or whatever uv accepts that keeps `opencv-python` out), `uv.lock`
- Create: `bench/stacks/mineru.py`

**Interfaces:**
- Consumes: the stack module contract and `_worker.main` (Task 5)
- Produces: `bench/stacks/mineru.py` with `SUFFIXES = frozenset({".pdf"})`.
  - `page_texts`: each page rendered with pypdfium2 at scale 2 and run through MinerU2.5 two-step extraction on the MLX backend, using the MinerU2.5-Pro 2605 1.2B checkpoint the spike used. Check its exact HF id and the client API in `mineru_vl_utils` before coding. A page's blocks are joined in reading order as Markdown.
  - `extract`: each page's Markdown → a one-page `DoclingDocument` through docling's Markdown backend → `extract_worker.chunk_document(doc, page=n)`. Every chunk cites its page.

- [ ] **Step 1: Record the before state.** Copy `uv.lock` into the plan workspace; `uv run python -c "import cv2; print(cv2.__version__)"` → note it (5.0.x).

- [ ] **Step 2: Add the extra and the override, then `uv lock && uv sync --extra bench --extra dev`.** The guard hook blocks edits whose text contains `mlx==`; `mineru-vl-utils[mlx]==` does not match it, so it should go through. If it is blocked, stop and report.

- [ ] **Step 3: Verify nothing existing changed.** Comparing package name/version pairs between the saved lock and `uv.lock` shows **only added** packages. `cv2.__version__` is unchanged. `uv run pytest -q` passes as before. If any existing package changed version, revert `pyproject.toml` and `uv.lock` and report.

- [ ] **Step 4: Implement `mineru.py` and smoke-test both modes on one apd image page:** page mode gives one line with non-empty `text`; worker mode (the Task 5 printf check) gives `{"status": "ok"}`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock bench/stacks/mineru.py
git commit -m "feat(bench): MinerU2.5 MLX stack behind an optional bench extra"
```

---

### Task 7: Index bench (interleaved stacks through the real pipeline)

**Files:**
- Create: `bench/index/__init__.py`, `bench/index/run.py`, `tests/bench/test_index_run.py`

**Interfaces:**
- Consumes: `run` (Task 1), `append`/`new_session`/`machine`/`versions` (Task 2), stack modules (Task 5)
- Produces:
  - `schedule(stacks: list[str], runs: int) -> list[tuple[str, int]]`: interleaved, `[(s0,0),(s1,0),(s0,1),(s1,1),…]`
  - `scratch_home(models: Path = Path.home() / ".groundly/models") -> Path`: a `tempfile.mkdtemp` with `models` symlinked in and nothing else
  - `worker_argv(stack: str, memray_out: Path | None) -> list[str]`: `[sys.executable, "-m", f"bench.stacks.{stack}", "--serve"]`, or with memray `[sys.executable, "-m", "memray", "run", "-q", "-o", str(memray_out), "-m", f"bench.stacks.{stack}", "--serve"]`
  - `store_stats(home: Path, subject: str) -> dict`: `{"chunks": int, "materials": int, "failed": [{"filename", "error"}]}` from `store.db`
  - Child mode `python -m bench.index.run --one --stack S --subject N --input P [--memray-out F]`, run with `GROUNDLY_HOME` set by the parent. It initializes the subject as `groundly index` does (`Subject(N)`, then `initialize()` if it does not exist), runs `IngestionPipeline(subj, extractor=SubprocessExtractor(load_settings().ingestion, argv=worker_argv(S, F)), on_event=…).run([P])` with the extractor closed afterwards (context manager), and prints one JSON object `{"pipeline_s", "extract_s", "embed_s", "files"}`. `extract_s` sums each file's time from `"extracting"` to `"embedding"`; `embed_s` sums from `"embedding"` to its terminal status.
  - Parent CLI: `python -m bench.index.run --stacks docling_standard docling_routed --runs 5 --subject apd --input ~/.groundly/apd/materials [--keep-homes] [--memray]`. For each scheduled run: a scratch home, then `monitor.run([sys.executable, "-m", "bench.index.run", "--one", …], env={**os.environ, "GROUNDLY_HOME": home, "PYTHONPATH": repo_root})` (the tree peak covers runner plus worker), then `store_stats`. It appends an `index` row (`arm=stack`; metrics `wall_s, pipeline_s, extract_s, embed_s, peak_rss_mb, chunks, materials, failed_count`; `failed` in the row as filenames and errors only) and deletes the home unless `--keep-homes` (kept homes print their path). With `--memray`, each run's worker profile goes to `bench/results/memray/<session>-<stack>-<run>.bin`.

- [ ] **Step 1: Write the failing tests**

```python
def test_schedule_alternates():
    assert schedule(["a", "b"], 2) == [("a", 0), ("b", 0), ("a", 1), ("b", 1)]


def test_scratch_home_holds_only_the_models_link(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    home = scratch_home(models)
    assert [p.name for p in home.iterdir()] == ["models"]
    assert (home / "models").resolve() == models.resolve()


def test_worker_argv_plain_and_memray(tmp_path):
    assert worker_argv("x", None) == [sys.executable, "-m", "bench.stacks.x", "--serve"]
    assert worker_argv("x", tmp_path / "w.bin") == [
        sys.executable,
        "-m",
        "memray",
        "run",
        "-q",
        "-o",
        str(tmp_path / "w.bin"),
        "-m",
        "bench.stacks.x",
        "--serve",
    ]


def test_store_stats_reads_chunks_and_failures(tmp_path):
    # schema via groundly's SubjectStore, rows by raw SQL (ruling R4): 1 indexed material with
    # 3 chunks, 1 extraction_failed "big.pdf" with error "extraction timed out after 300s"
    stats = store_stats(tmp_path, "s")
    assert stats["chunks"] == 3 and stats["materials"] == 2
    assert stats["failed"] == [{"filename": "big.pdf", "error": "extraction timed out after 300s"}]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/bench/test_index_run.py -v`
Expected: FAIL, import error

- [ ] **Step 3: Implement `run.py`** to the interfaces above.

- [ ] **Step 4: Run the tests, then a one-run smoke test**

Run: `uv run pytest tests/bench/test_index_run.py -v` → 4 passed.
Then `uv run python -m bench.index.run --stacks docling_standard docling_routed --runs 1 --subject smoke --input <dir with 2 small apd PDFs>` → two `index` rows, both `returncode` 0 and `chunks` > 0. Then once with `--memray` → a `.bin` per run and still `returncode` 0, which proves `-q` keeps the reply protocol clean.

- [ ] **Step 5: Commit**

```bash
git add bench/index tests/bench
git commit -m "feat(bench): interleaved index runs of stacks through the real pipeline"
```

---

### Task 8: Retrieval bench

**Files:**
- Create: `bench/retrieval/__init__.py`, `bench/retrieval/run.py`, `bench/retrieval/queries/apd.json` (copied unchanged from `~/Developer/groundly-rs/bench/extract-alt/route/queries_labeled.json`), `tests/bench/test_retrieval.py`

**Interfaces:**
- Consumes: `append`/`new_session`/`machine`/`versions` (Task 2)
- Produces:
  - `metrics(labelled: list[dict], hits: list[list[tuple[str, int | None]]], k: int = 8) -> dict[str, float]`: `hit_exact@8`, `hit_within1@8`, `mrr_within1@8`; a hit matches when the filename is equal and the page is equal (exact) or `abs(page - label) <= 1` (within1)
  - CLI: `python -m bench.retrieval.run --home H --subject S --queries bench/retrieval/queries/S.json --arm NAME`. Sets `GROUNDLY_HOME=H` before importing `groundly`, calls `groundly.retrieval.vector.search(S, q, k=8)` per query, appends a `retrieval` row.

- [ ] **Step 1: Write the failing test**

```python
def test_metrics_exact_and_within_one():
    labelled = [
        {"query": "q1", "file": "a.pdf", "page": 5},
        {"query": "q2", "file": "b.pdf", "page": 2},
    ]
    hits = [[("a.pdf", 6), ("a.pdf", 5)], [("c.pdf", 2)]]
    m = metrics(labelled, hits)
    assert m["hit_exact@8"] == 0.5 and m["hit_within1@8"] == 0.5 and m["mrr_within1@8"] == 0.5
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/bench/test_retrieval.py -v`
Expected: FAIL, import error

- [ ] **Step 3: Implement `run.py`**; copy the apd query file byte for byte.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/bench/test_retrieval.py -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add bench/retrieval tests/bench
git commit -m "feat(bench): retrieval bench with the apd labelled set"
```

---

### Task 9: Settle the speed gate (measurement, no new code)

**Files:**
- Create: `bench/README.md` (how to run each bench, what each metric means, the stack contract, the memray caveat)

- [ ] **Step 1: Nothing else heavy running.** Close other indexing; `pmset -g` shows AC power. Note the time.
- [ ] **Step 2: Gate runs.** `uv run python -m bench.index.run --stacks docling_standard docling_routed --runs 5 --subject apd --input ~/.groundly/apd/materials --keep-homes` (about 40 min, run in the background). Expected: 10 `index` rows with `returncode` 0, chunk counts near 1,197 (standard) and 1,343 (routed).
- [ ] **Step 3: Verdict.** `uv run python -m bench.common.compare index --baseline docling_standard --gate 0.85` → paste the table (`wall_s`, then `--metric extract_s` and `--metric embed_s`).
- [ ] **Step 4: Retrieval on one kept home per stack.** `python -m bench.retrieval.run --home <kept standard home> --subject apd --queries bench/retrieval/queries/apd.json --arm docling_standard`, same for routed; compare `--baseline docling_standard --metric hit_within1@8`. Expected near the spike: within one page 0.98 for both, exact 0.80 (standard) vs 0.94 (routed). Then delete the kept homes. One `--memray` run per stack on apd for the worker memory profiles.
- [ ] **Step 5: New subjects and timeouts.** `uv run python -m bench.extract.sample --version 1` prints RO/EN shares per subject. Pick the most RO and the most EN subject among gac, lftc, pdss, pm, romsilva (pi and bia are excluded on size). Run `bench.index.run --stacks docling_standard docling_routed --runs 1` on each with `--input ~/.groundly/<s>/materials`. Then run each of the three timed-out files alone (`LFTC Cursuri.pdf`, `PI_Slide-uri_3.pdf`, `NT1 - Compozitii, scheme si tehnologii de regenerare.pdf`) as its own `--subject timeout-<n>` run. Label each timeout router-caused, shared, or neither.
- [ ] **Step 6: Report to Paul** with the gate table, the retrieval numbers and the timeout labels. Restating decision 49's gate is Paul's call via `/decision`; do not edit decision 49 here.
- [ ] **Step 7: Commit** `bench/README.md` and `bench/corpus/manifest.json`.

```bash
git add bench/README.md bench/corpus/manifest.json
git commit -m "docs(bench): README and corpus manifest v1"
```

---

### Task 10: Accuracy baseline (needs Paul's corrections)

- [ ] **Step 1: Draft the reference pages.** `PYTHONPATH=. uv run python -m bench.stacks.mineru --pages bench/corpus/manifest.json --only-ref --out <workspace>/ref.jsonl`. Write each page's text to `bench/reference/<sha256>-<page>.draft.txt`, and the page rendered at scale 2 next to it as `.png`.
- [ ] **Step 2: Hand off to Paul.** He corrects each draft against its PNG and saves it as `<sha256>-<page>.txt` (about an hour). Wait.
- [ ] **Step 3: Extract runs.** `bench.extract.run --stack docling_standard`, `--stack docling_routed`, `--stack mineru` (one session per invocation is fine for accuracy; the speed gate is Task 9's). Expected: `failed_pages` 0 (or explained), `scored_pages` = the manifest size.
- [ ] **Step 4: RO retrieval set.** Write about 30 labelled queries `{query, file, page}` on the most-RO subject chosen in Task 9, in the apd set's style, to `bench/retrieval/queries/<subject>.json`. Paul spot-checks them. Run the retrieval bench on a fresh index of that subject from each stack (`bench.index.run --stacks docling_standard docling_routed --runs 1 --keep-homes`).
- [ ] **Step 5: Report** per stack: word F1 / bigram F1 / diacritic recall (plain and folded) per kind × language, plus RO retrieval, compared with `--baseline docling_standard`.
- [ ] **Step 6: Commit** the RO query file.

```bash
git add bench/retrieval/queries
git commit -m "feat(bench): Romanian labelled retrieval set"
```
