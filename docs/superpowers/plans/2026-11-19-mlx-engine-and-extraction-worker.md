# MLX Engine and Extraction Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Index a course in one warm extraction worker instead of one process per file, and run bge-m3 and the reranker on MLX on Apple Silicon so no long-running process ever imports torch.

**Architecture:** Part 1 turns `extract_worker.py` into a line-protocol server that `SubprocessExtractor` starts once per indexing run and restarts after a crash or timeout. Part 2 adds an MLX backend that duck-types FlagEmbedding's `encode` / `compute_score`, chosen in `_build()` when `import mlx.core` works; bge-m3's torch pickles are converted to safetensors once, in a child process. The two parts share no code.

**Tech Stack:** Python ≥3.11, MLX (`mlx.core`), `tokenizers`, `safetensors`, docling 2.115 (unchanged), pytest.

**Spec:** `docs/superpowers/specs/2026-11-19-mlx-engine-and-extraction-worker-design.md`

## Global Constraints

- torch, FlagEmbedding and sentence-transformers are never imported in a process that loaded the MLX backend; any torch use (weight conversion) runs in a child process.
- The embedding pin is unchanged: `EMBEDDING_MODEL`, `HF_REVISION`, dim 1024, dtype `float16`, normalized. No manifest change.
- MLX tolerance against torch: dense cosine ≥ 0.9997 per chunk, sparse top-10 identical, rerank top-5 identical on the 50 spike queries.
- `mlx==<current exact>` (0.32.4 on PyPI today) with marker `sys_platform == 'darwin' and platform_machine == 'arm64' and platform_release >= '23'`, added to `.claude/hooks/guard-pins.sh`.
- MLX packs inputs end to end up to `PACK_TOKENS = 1024` tokens per forward pass; buffer cache capped at `CACHE_LIMIT_BYTES = 1 << 30`.
- No config setting chooses the engine. A Mac without importable MLX runs the torch backend.
- Extraction failure messages are byte-for-byte today's (`extract.py`): "extraction timed out after {N}s", "parser failed: …", "no readable text — OCR found nothing to extract", "no extractable text", "extraction output too large", the image-too-large message, and `ModelUnavailable` stays retryable with no row.
- Worker controls stay: argv exec, no shell, temp working dir, output file size cap `MAX_EXTRACTION_JSON_BYTES`, stderr to a file with only a tail read, `GROUNDLY_LOG_LEVEL` removed from the worker env.
- One worker per indexing run; never kept between runs. Windows-safe: no `select()` on pipes.
- Lazy loading: nothing loads a model or starts a worker at import or spawn.
- Commits on branch `mlx-engine`, never `main`. Run `graphify update .` after code changes.

## Review Focus

1. A library prints to stdout inside the worker (docling, RapidOCR, a C extension writing to fd 1) → the protocol must not break. Test in Task 1.
2. The worker exits between files (OOM kill while idle, a crash right after replying) → the next file restarts it and indexes normally. Test in Task 2.
3. `groundly start` serves a search while indexing or another search runs (FastMCP threadpool) → MLX forwards are serialized, results equal sequential ones. Test in Task 4.
4. A conversion killed halfway leaves a partial directory → the next load never reads a truncated file; it converts again. Test in Task 3.
5. Edge inputs to the MLX embedder: an empty list, an empty string, a text longer than 8,192 tokens → `([], [])`, a valid vector, a truncated valid vector. Test in Task 4.

---

## Part 1 — Long-lived extraction worker

### Task 1: Worker serve mode

**Files:**
- Modify: `groundly/ingestion/extract_worker.py`
- Test: `tests/ingestion/test_extract_worker_serve.py`

**Interfaces:**
- Produces: `python -m groundly.ingestion.extract_worker --serve`; protocol, one JSON object per line.
  - Request (stdin): `{"in": "<abs path>", "out": "<abs path>", "ocr_lang": "<str>" | null}`
  - Reply (stdout): `{"status": "ok" | "no_text" | "model_unavailable" | "input_too_large" | "error"}`
  - Cause text is written to stderr before the reply, as today; the parent reads it.
- Produces: `serve(stdin: TextIO, stdout: TextIO, handle: Callable[[Path, Path, str | None], None] = _handle) -> None` and `_handle(in_path, out_path, ocr_lang)` (today's `main()` body after argv parsing: extract, `EXIT_NO_TEXT` check, write JSON).
- One-shot `main()` keeps working unchanged (`<in> <out.json> [ocr_lang]`).

- [ ] **Step 1: Write the failing tests**

```python
import io, json
from groundly.ingestion import extract_worker as w

def _run(handler, n):
    reqs = "".join(json.dumps({"in": f"/x{i}", "out": f"/o{i}", "ocr_lang": None}) + "\n" for i in range(n))
    out = io.StringIO()
    w.serve(io.StringIO(reqs), out, handle=handler)
    return [json.loads(line)["status"] for line in out.getvalue().splitlines()]

def test_serve_maps_each_outcome_to_a_status_and_keeps_going():
    outcomes = iter([None, SystemExit(w.EXIT_NO_TEXT), SystemExit(w.EXIT_MODEL_UNAVAILABLE),
                     SystemExit(w.EXIT_INPUT_TOO_LARGE), SystemExit("not a readable nbformat 4 notebook"),
                     ValueError("docling blew up"), None])
    def handler(i, o, lang):
        e = next(outcomes)
        if e: raise e
    assert _run(handler, 7) == ["ok", "no_text", "model_unavailable", "input_too_large", "error", "error", "ok"]

def test_serve_writes_the_cause_to_stderr_before_replying(capsys):
    def handler(i, o, lang): raise SystemExit("not a readable nbformat 4 notebook: boom")
    _run(handler, 1)
    assert capsys.readouterr().err.strip().splitlines()[-1].endswith("boom")

def test_library_stdout_never_reaches_the_protocol_stream():  # Review Focus 1
    import subprocess, sys
    code = textwrap.dedent("""
        import os
        from groundly.ingestion import extract_worker as w
        def h(i, o, lang):
            print("noise")
            os.write(1, b"raw noise\\n")
        w.serve_stdio(handle=h)
    """)
    req = json.dumps({"in": "/a", "out": "/b", "ocr_lang": None}) + "\n"
    r = subprocess.run([sys.executable, "-c", code], input=req, capture_output=True, text=True, check=True)
    assert [json.loads(l)["status"] for l in r.stdout.splitlines()] == ["ok"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/ingestion/test_extract_worker_serve.py -v`
Expected: FAIL with `AttributeError: ... has no attribute 'serve'`

- [ ] **Step 3: Implement `serve`, `serve_stdio`, `_handle` and caching in `extract_worker.py`**

- `serve` catches `SystemExit` (int code → status by `{EXIT_NO_TEXT: "no_text", EXIT_MODEL_UNAVAILABLE: "model_unavailable", EXIT_INPUT_TOO_LARGE: "input_too_large"}`, a `str` code → print it to stderr, `"error"`) and `Exception` (`traceback.print_exc()`, `"error"`), flushes stderr, then writes and flushes the reply line.
- `serve_stdio(handle=_handle)`: `reply = os.fdopen(os.dup(1), "w")`, then `os.dup2(2, 1)` and `sys.stdout = sys.stderr`, so anything a library prints goes to stderr; then `serve(sys.stdin, reply, handle)`.
- `main()` dispatches to `serve_stdio()` when `sys.argv[1] == "--serve"`.
- Make repeated files cheap: `functools.cache` on `_bge_m3_tokenizer()` and on a new `_converter(ocr_lang: str | None) -> DocumentConverter` (today's options and format_options block); `_extract_docling` calls `_model_step(lambda: _converter(ocr_lang).initialize_pipeline(input_format))` per file (docling caches the pipeline inside the converter).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/ingestion/ -v`
Expected: PASS (the existing `test_extract_worker.py` tests also stay green)

- [ ] **Step 5: Commit**

```bash
git add groundly/ingestion/extract_worker.py tests/ingestion/test_extract_worker_serve.py
git commit -m "feat(ingestion): extraction worker serves many files over a line protocol"
```

### Task 2: One worker per indexing run

**Files:**
- Modify: `groundly/ingestion/extract.py`, `groundly/ingestion/pipeline.py:99-170`
- Create: `tests/ingestion/stub_worker.py`
- Test: `tests/ingestion/test_extract_session.py`

**Interfaces:**
- Consumes: Task 1's protocol and `--serve`.
- Produces: `SubprocessExtractor(settings: IngestionSettings | None = None, argv: list[str] | None = None)`; `argv` defaults to `[sys.executable, "-m", "groundly.ingestion.extract_worker", "--serve"]`. `extract(path, ocr_lang=None) -> Extraction` unchanged. New `close() -> None` (idempotent; closes stdin, waits briefly, kills if needed), `__enter__` / `__exit__`. The live worker is `self._proc: subprocess.Popen | None` (tests read it).
- Produces: `IngestionPipeline.run` closes an extractor it created itself (not an injected one) in a `finally`.

- [ ] **Step 1: Write the stub worker and failing tests**

`tests/ingestion/stub_worker.py` speaks the protocol and acts on the input file's name: `crash*` → `os._exit(9)` before replying; `hang*` → `time.sleep(60)`; `notext*` → `no_text`; `bye*` → reply `ok` then `os._exit(0)`; anything else → write `{"pages": 1, "chunks": [{"text": "<pid>", "heading_path": null, "page": 1, "token_count": 1}]}` to `out` and reply `ok`.

```python
STUB = [sys.executable, str(Path(__file__).parent / "stub_worker.py")]

def _ex(timeout=30):
    return SubprocessExtractor(IngestionSettings(timeout_seconds=timeout), argv=STUB)

def _pid(extraction): return extraction.chunks[0].text

def test_one_worker_serves_the_whole_run(tmp_path):
    files = [tmp_path / f"f{i}.txt" for i in range(3)]
    for f in files: f.write_text("x")
    with _ex() as ex:
        assert len({_pid(ex.extract(f)) for f in files}) == 1

def test_a_crash_fails_only_that_file_and_a_fresh_worker_takes_the_next(tmp_path):
    (tmp_path / "crash.pdf").write_text("x"); (tmp_path / "ok.txt").write_text("x")
    with _ex() as ex:
        with pytest.raises(ExtractionFailure, match="^parser failed: "):
            ex.extract(tmp_path / "crash.pdf")
        assert ex.extract(tmp_path / "ok.txt").chunks

def test_a_timeout_names_the_limit_and_the_next_file_still_indexes(tmp_path):
    (tmp_path / "hang.pdf").write_text("x"); (tmp_path / "ok.txt").write_text("x")
    with _ex(timeout=1) as ex:
        with pytest.raises(ExtractionFailure, match="^extraction timed out after 1s$"):
            ex.extract(tmp_path / "hang.pdf")
        assert ex.extract(tmp_path / "ok.txt").chunks

def test_no_text_keeps_todays_message(tmp_path):
    (tmp_path / "notext.pdf").write_text("x")
    with _ex() as ex, pytest.raises(ExtractionFailure, match="^no readable text — OCR found nothing to extract$"):
        ex.extract(tmp_path / "notext.pdf")

def test_a_worker_that_exits_between_files_is_restarted(tmp_path):  # Review Focus 2
    (tmp_path / "bye.txt").write_text("x"); (tmp_path / "ok.txt").write_text("x")
    with _ex() as ex:
        ex.extract(tmp_path / "bye.txt")
        assert ex.extract(tmp_path / "ok.txt").chunks

def test_close_leaves_no_worker_running(tmp_path):
    (tmp_path / "ok.txt").write_text("x")
    ex = _ex(); ex.extract(tmp_path / "ok.txt"); proc = ex._proc
    ex.close(); ex.close()
    assert proc.poll() is not None
```

Plus in `tests/ingestion/test_pipeline.py`: `test_run_closes_the_extractor_it_created` (patch `SubprocessExtractor` in `pipeline` with a fake recording `close()`; an injected extractor is not closed).

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/ingestion/test_extract_session.py -v`
Expected: FAIL with `TypeError: ... unexpected keyword argument 'argv'`

- [ ] **Step 3: Implement the worker session in `SubprocessExtractor`**

- Start lazily in `extract()`: `subprocess.Popen(argv, stdin=PIPE, stdout=PIPE, stderr=<file in the session temp dir>, cwd=<session temp dir>, env=<today's env>, text=True, bufsize=1)`; one `tempfile.TemporaryDirectory` per worker lifetime holds `stderr.log` and each file's `extraction.json` (unlinked before each request).
- Read replies on a daemon thread that puts each stdout line (or `None` at EOF) on a `queue.Queue`; `extract()` waits with `queue.get(timeout=self.timeout)`. Windows has no `select()` on pipes.
- Per request, note `stderr.log`'s size first; the cause is the last non-empty line written after that offset (generalize `_stderr_tail(path, start)`).
- `queue.Empty` → kill the worker, raise the timeout failure. EOF or a broken pipe on write → reap, raise `parser failed: {tail or f'exit code {rc}'}`. Either way the next `extract()` starts a fresh worker.
- Status → today's exception mapping; `ok` → today's size cap and `json.loads`.
- The file-size guard stays before any worker starts.
- `IngestionPipeline.__init__` records `self._owns_extractor = extractor is None`; `run()` wraps its loop in `try/finally` and calls `self.extractor.close()` when it owns it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/ingestion/ tests/cli/ -v`
Expected: PASS, including `test_extract_limits.py`, which runs the real worker.

- [ ] **Step 5: Run the slow ingestion suite against the real worker**

Run: `uv run pytest -m slow tests/ingestion/ -v`
Expected: PASS (`test_title_only_image_salvages_heading_text` and the pipeline slow tests)

- [ ] **Step 6: Commit**

```bash
git add groundly/ingestion/extract.py groundly/ingestion/pipeline.py tests/ingestion/
git commit -m "feat(ingestion): one warm extraction worker per indexing run, restarted after a crash or timeout"
```

---

## Part 2 — MLX backend on Apple Silicon

### Task 3: Dependency pin and torch-free weights

**Files:**
- Modify: `pyproject.toml` (dependencies), `uv.lock`, `.claude/hooks/guard-pins.sh`
- Create: `groundly/llm/convert.py`
- Test: `tests/llm/test_llm_convert.py`

**Interfaces:**
- Produces: `converted_dir(revision: str = HF_REVISION) -> Path` = `groundly_home() / "models" / f"bge-m3-{revision}"`.
- Produces: `ensure_converted(snapshot: Path, revision: str = HF_REVISION) -> Path`: returns a directory holding `model.safetensors` (bge-m3 encoder, keys as in `pytorch_model.bin`, f16) and `sparse_linear.safetensors` (keys `weight`, `bias`, f16). Converts by running `[sys.executable, "-m", "groundly.llm.convert", str(snapshot), str(tmp_dir)]` into a sibling temp dir, then `os.replace` onto the final name. A directory exists only when complete.
- Produces: `python -m groundly.llm.convert <snapshot> <out_dir>`: `torch.load(..., weights_only=True)`, cast to float16, `safetensors.torch.save_file`.
- Raises `ModelDownloadError` naming bge-m3 when the child exits non-zero.
- `groundly models uninstall` also removes `converted_dir()` when it exists (spec: `groundly models` covers the converted files); `install` stays download-only, conversion happens on first use.

- [ ] **Step 1: Write the failing tests**

```python
def _fake_snapshot(tmp_path):  # runs torch in a child, never in the test process
    snap = tmp_path / "snap"; snap.mkdir()
    code = ("import torch,sys; p=sys.argv[1];"
            "torch.save({'embeddings.word_embeddings.weight': torch.ones(4, 2)}, p + '/pytorch_model.bin');"
            "torch.save({'weight': torch.ones(1, 2), 'bias': torch.zeros(1)}, p + '/sparse_linear.pt')")
    subprocess.run([sys.executable, "-c", code, str(snap)], check=True)
    return snap

def test_converts_once_to_f16_safetensors(tmp_path, monkeypatch):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    out = convert.ensure_converted(_fake_snapshot(tmp_path), revision="r1")
    from safetensors.numpy import load_file
    enc, head = load_file(out / "model.safetensors"), load_file(out / "sparse_linear.safetensors")
    assert enc["embeddings.word_embeddings.weight"].dtype == np.float16
    assert set(head) == {"weight", "bias"}
    mtime = (out / "model.safetensors").stat().st_mtime_ns
    assert convert.ensure_converted(tmp_path / "snap", revision="r1") == out
    assert (out / "model.safetensors").stat().st_mtime_ns == mtime  # reused, not reconverted

def test_a_half_finished_conversion_is_never_used(tmp_path, monkeypatch):  # Review Focus 4
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    partial = convert.converted_dir("r1").parent / "bge-m3-r1.tmp-dead"
    partial.mkdir(parents=True); (partial / "model.safetensors").write_bytes(b"trunc")
    out = convert.ensure_converted(_fake_snapshot(tmp_path), revision="r1")
    assert out == convert.converted_dir("r1") and (out / "sparse_linear.safetensors").exists()

def test_conversion_never_imports_torch_in_the_caller(tmp_path):
    probe = ("import sys, groundly.llm.convert as c; from pathlib import Path;"
             f"c.ensure_converted(Path({str(_fake_snapshot(tmp_path))!r}), revision='r2'); print('torch' in sys.modules)")
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True,
                       env={**os.environ, "GROUNDLY_HOME": str(tmp_path / "home")})
    assert r.stdout.strip() == "False"

def test_a_failed_conversion_names_the_model(tmp_path, monkeypatch):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "empty").mkdir()
    with pytest.raises(ModelDownloadError, match="bge-m3"):
        convert.ensure_converted(tmp_path / "empty", revision="r3")
```

Plus in `tests/cli/test_cli_models.py`: `test_uninstall_removes_converted_weights` (with `GROUNDLY_HOME` in tmp, a `converted_dir()` holding a file, and `cached_snapshot` patched to a fake path, `groundly models uninstall -y` leaves `converted_dir()` absent).

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/llm/test_llm_convert.py tests/cli/test_cli_models.py -v`
Expected: FAIL with `ModuleNotFoundError: groundly.llm.convert`

- [ ] **Step 3: Implement `groundly/llm/convert.py`; add the pin**

Add `"mlx==0.32.4; sys_platform == 'darwin' and platform_machine == 'arm64' and platform_release >= '23'"` to `pyproject.toml` (use the current release if newer), run `uv lock && uv sync`, and add `mlx` to the guarded names in `guard-pins.sh`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/llm/test_llm_convert.py -v && uv run lint-imports`
Expected: PASS; import contracts kept.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock .claude/hooks/guard-pins.sh groundly/llm/convert.py groundly/cli/models.py tests/llm/test_llm_convert.py tests/cli/test_cli_models.py
git commit -m "feat(llm): pin mlx on Apple Silicon; convert bge-m3 weights to safetensors in a child process"
```

### Task 4: MLX forward and backends, chosen at build time

**Files:**
- Create: `groundly/llm/mlx_xlmr.py`
- Modify: `groundly/llm/embeddings.py` (`LazyModel.unload`, `BgeM3Embedder._build`), `groundly/llm/rerank.py` (`BgeReranker._build`)
- Test: `tests/llm/test_llm_mlx.py`, `tests/llm/test_llm_embeddings.py`, `tests/llm/test_llm_rerank.py`, `tests/llm/test_slow_models.py`

**Interfaces:**
- Consumes: Task 3's `ensure_converted(snapshot) -> Path`.
- Produces in `mlx_xlmr.py` (importable without MLX; `mlx` is imported inside functions):
  - `PACK_TOKENS = 1024`, `CACHE_LIMIT_BYTES = 1 << 30`, `SPECIAL_IDS = {0, 1, 2, 3}`
  - `mlx_available() -> bool` (the import of `mlx.core` succeeds)
  - `pack_groups(lens: list[int], budget: int = PACK_TOKENS) -> list[list[int]]`: consecutive indices, each group's total ≤ budget, an input longer than the budget alone in its group.
  - `forward(weights: dict, ids: list[int], lens: list[int], heads: int = 16) -> mx.array` returning `[sum(lens), hidden]`. Port of `~/Developer/groundly-rs/bench/py/mlx_py.py:forward`, with layer count and hidden size read from the weights (the tiny test model needs that).
  - `MlxBgeM3(model_dir: Path, tokenizer_json: Path)` with `encode(texts, batch_size=..., return_dense=True, return_sparse=True) -> {"dense_vecs": np.ndarray [n, 1024] float32, "lexical_weights": list[dict[int, float]]}`. Duck-types FlagEmbedding's `BGEM3FlagModel.encode`, so `BgeM3Embedder.encode` and `encode_stream` stay as they are; `batch_size` is ignored (packing replaces it). Truncates at 8,192 tokens. Dense = L2-normalized CLS; sparse = `relu(hidden @ W.T + b)`, `SPECIAL_IDS` dropped, max weight per token id, weights > 0 only.
  - `MlxReranker(model_dir: Path)` with `compute_score(pairs, batch_size=...) -> list[float]`, truncating each pair at 512 tokens, weights prefix `roberta.` stripped, head `classifier.dense` → tanh → `classifier.out_proj`.
  - Both hold a `threading.Lock` around each forward; both call `mx.set_cache_limit(CACHE_LIMIT_BYTES)` at construction.
- `embeddings.py` imports `mlx_available`, `MlxBgeM3` (from `mlx_xlmr`) and `ensure_converted` (from `convert`) at module level; `rerank.py` imports `mlx_available` and `MlxReranker`. Tests patch them by those module paths. This is safe at spawn because `mlx_xlmr` imports `mlx` only inside functions.
- `BgeM3Embedder._build` / `BgeReranker._build`: `if mlx_available(): return MlxBgeM3(...)` / `MlxReranker(...)`, else today's FlagEmbedding code; failures still wrap in `ModelDownloadError`.
- `LazyModel.unload` also calls `mlx.core.clear_cache()` when `"mlx.core" in sys.modules`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/llm/test_llm_mlx.py
def test_pack_groups_respects_the_budget_and_keeps_order():
    assert pack_groups([600, 400, 30, 2000, 10], budget=1024) == [[0, 1], [2], [3], [4]]
    assert pack_groups([], budget=1024) == []

needs_mlx = pytest.mark.skipif(not mlx_available(), reason="MLX needs Apple Silicon")

@needs_mlx
def test_packed_forward_equals_one_input_per_pass():
    # random 2-layer, hidden 32, 2-head XLM-R with the bge-m3 key names
    w = _tiny_weights(layers=2, hidden=32, vocab=50)
    a, b = [0, 5, 7, 2], [0, 9, 2]
    packed = np.array(forward(w, a + b, [4, 3], heads=2))
    alone = np.concatenate([np.array(forward(w, a, [4], heads=2)), np.array(forward(w, b, [3], heads=2))])
    assert np.allclose(packed, alone, atol=1e-2)
```

```python
# tests/llm/test_llm_embeddings.py and test_llm_rerank.py, mirroring the FlagEmbedding wrapping tests
def test_build_uses_mlx_when_available(monkeypatch):
    monkeypatch.setattr("groundly.llm.embeddings.ensure_downloaded", lambda *a, **k: Path("/fake"))
    monkeypatch.setattr("groundly.llm.embeddings.mlx_available", lambda: True)
    monkeypatch.setattr("groundly.llm.embeddings.ensure_converted", lambda *a, **k: Path("/conv"))
    monkeypatch.setattr("groundly.llm.embeddings.MlxBgeM3", lambda *a, **k: "mlx-model")
    with BgeM3Embedder()._use() as model:
        assert model == "mlx-model"

def test_build_falls_back_to_flagembedding_without_mlx(monkeypatch):
    # mlx_available -> False; the existing FlagEmbedding-import-failure test then still raises ModelDownloadError
```

```python
# tests/llm/test_slow_models.py (slow; the MLX ones also skip without MLX)
@needs_mlx
def test_mlx_matches_torch_within_the_pin_tolerance():
    texts = [<32 apd-like chunk texts, Romanian and English, one > 8,192 tokens>]
    mlx_out = MlxBgeM3(...).encode(texts)        # via ensure_converted
    torch_out = BGEM3FlagModel(snapshot, use_fp16=True).encode(texts, batch_size=1, return_dense=True, return_sparse=True)
    assert min(cosine(a, b) for a, b in zip(mlx_out["dense_vecs"], torch_out["dense_vecs"])) >= 0.9997
    assert all(top10(m) == top10({int(k): v for k, v in t.items()})
               for m, t in zip(mlx_out["lexical_weights"], torch_out["lexical_weights"]))

@needs_mlx
def test_mlx_reranker_keeps_flagreranker_order():
    # 3 queries × 20 passages: top-5 sets identical, max |score diff| ≤ 0.03

@needs_mlx
def test_mlx_edge_inputs():  # Review Focus 5
    out = MlxBgeM3(...).encode([])
    assert out["dense_vecs"].shape == (0, 1024) and out["lexical_weights"] == []
    out = MlxBgeM3(...).encode(["", "x " * 20_000])
    assert out["dense_vecs"].shape == (2, 1024) and np.isfinite(out["dense_vecs"]).all()

@needs_mlx
def test_concurrent_encodes_equal_sequential():  # Review Focus 3
    m = BgeM3Embedder(); texts = ["mutex", "semafor", "deadlock", "barieră"]
    expected = m.encode(texts)[0]
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: m.encode(texts)[0], range(8)))
    assert all(np.allclose(r, expected, atol=1e-6) for r in results)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/llm/ -v && uv run pytest -m slow tests/llm/ -v`
Expected: FAIL with `ModuleNotFoundError: groundly.llm.mlx_xlmr`

- [ ] **Step 3: Implement `mlx_xlmr.py` and wire `_build()` / `unload()`**

Port the forward, packing, CLS/sparse/classifier heads and cache cap from `~/Developer/groundly-rs/bench/py/mlx_py.py`. Tokenize with `tokenizers.Tokenizer.from_file(<snapshot>/tokenizer.json)`; pairs with `tok.encode(query, passage)`. The model reads only from `ensure_converted(snapshot)` (bge-m3) and the reranker snapshot's `model.safetensors`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/llm/ -v && uv run pytest -m slow tests/llm/ -v`
Expected: PASS on Apple Silicon. On Linux the MLX tests skip and the rest pass.

- [ ] **Step 5: Commit**

```bash
git add groundly/llm/ tests/llm/
git commit -m "feat(llm): bge-m3 and the reranker on MLX on Apple Silicon, torch elsewhere"
```

### Task 5: torch stays out of the serving process; idle unload frees MLX memory

**Files:**
- Test: `tests/llm/test_slow_models.py`

**Interfaces:**
- Consumes: `shared_embedder()`, `shared_reranker()`, `LazyModel.unload()` (Task 4).

- [ ] **Step 1: Write the tests (slow, skip without MLX)**

```python
@needs_mlx
def test_search_path_never_imports_torch():
    """Search, the topic gate and import re-embedding all reach the models through these two
    singletons, so one probe covers them. A subprocess: never pop torch from sys.modules."""
    probe = ("import sys; from groundly.llm.embeddings import shared_embedder;"
             "from groundly.llm.rerank import shared_reranker;"
             "shared_embedder().encode(['x']); list(shared_embedder().encode_stream(['y']));"
             "shared_reranker().compute_score([('a', 'b')]);"
             "print(','.join(m for m in ('torch', 'FlagEmbedding', 'sentence_transformers') if m in sys.modules))")
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert r.stdout.strip() == ""

@needs_mlx
def test_idle_unload_returns_mlx_memory():
    import mlx.core as mx
    m = BgeM3Embedder(); m.encode(["warm"])
    assert mx.get_active_memory() > 500 * 2**20
    m.unload()
    assert mx.get_active_memory() < 50 * 2**20 and mx.get_cache_memory() == 0
```

- [ ] **Step 2: Run them**

Run: `uv run pytest -m slow tests/llm/test_slow_models.py -v -k "torch or unload"`
Expected: PASS. If the first fails, the module it names is imported on the MLX path; find the import and move it behind the backend choice.

- [ ] **Step 3: Commit**

```bash
git add tests/llm/test_slow_models.py
git commit -m "test(llm): MLX path imports no torch and idle unload frees MLX memory"
```

### Task 6: Measure, record decision 48, update the docs

**Files:**
- Modify: `docs/groundly-spec.md` (§4 item 4, decision register entry 48), `docs/tech-stack/tech-stack.md` (engine per platform, mlx pin, tolerance), `docs/infrastructure/distribution.md` (line 28 "sequential per-file processing", Apple Silicon fast path, macOS 14 for MLX), `docs/architecture/overview.md` and `docs/architecture/retrieval.md` where they name FlagEmbedding as the only engine.
- Record numbers in the PR description; nothing under `tests/` depends on the spike repo.

- [ ] **Step 1: Measure on apd (spike workloads in `~/Developer/groundly-rs/bench/`)**

- Tolerance: every apd chunk (dense cosine ≥ 0.9997, sparse top-10) and the 50 search pools (`bench/rerank_workload.pools.json`: top-5 identical, max score diff).
- Topic-gate flips: `bench/py/gates.py` logic for apd, bgf and sma, run once per backend; count nodes whose accept/reject or source set differs.
- `groundly index` of a copy of apd (`GROUNDLY_HOME` pointed at a temp dir): wall time and chunk count, before (main) and after. Expected ≈ 3.5 min against 21.6 min, chunk count unchanged at 1,197 ± extraction noise.
- `search` p50 on the 50 queries (`bench/py/search_latency.py`). Expected ≈ 0.8 s against 1.35 s.

- [ ] **Step 2: Record the decision with `/decision`**

Decision 48: the engine per platform (MLX on Apple Silicon with macOS 14 or later, torch elsewhere), the pin unchanged with the measured tolerance as the contract an engine must stay within, the topic-gate flip count accepted (the gate shows topicality only), one warm extraction worker per run with per-file isolation, and the rejected options (torch-free extraction, Apple Vision OCR, an `engine` field in the pin) with their spike numbers. It supersedes decision 19's "fp16" wording only for the MLX path.

- [ ] **Step 3: Full verification**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pyrefly check && uv run lint-imports && uv run pytest && uv run pytest -m slow`
Expected: all green. Then `graphify update .`

- [ ] **Step 4: Commit**

```bash
git add docs/
git commit -m "docs: decision 48 — MLX engine on Apple Silicon and one extraction worker per run"
```
