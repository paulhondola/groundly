# MLX embedder/reranker on Apple Silicon + a long-lived extraction worker

Date: 2026-11-19 · Status: design, awaiting approval · Becomes decision 48 (`/decision`) when implemented.

## Intent

Make indexing and search faster on the machines Groundly is used on today (Apple Silicon), and keep torch out of
every process that stays running. Two independent changes, measured in the Rust-port spike
(`~/Developer/groundly-rs/bench/RESULTS.md`) and the torch-free extraction spike (`bench/torchfree/RESULTS.md`), both
on an M1 Pro against the apd course (187 files, 782 PDF pages, 1,197 chunks):

| | Today | After | Source |
|---|---|---|---|
| `groundly index` apd, end to end | 21.6 min | ~3.5 min (projected from measured parts) | worker: ~15 of 21.6 min is per-file process start + model reload |
| bge-m3, all 1,197 chunks | 73 s, load 7–10 s, 3.8 GB | 51–54 s, load 0.7–1.3 s, 2.0–2.3 GB | MLX from Python, packed ≤1,024 tokens/pass |
| Rerank one search's 20 candidates, p50 | 1.27 s | 0.77 s | MLX packed; reranking is 94% of a search |
| `search` end to end, p50 | 1.35 s | ~0.8 s | |
| Topic gate (`submit_tree`) apd, 80 nodes | 111 s | ~65 s | reranker-bound |
| Long-lived process after idle unload | ~1 GB (torch stays imported) | the interpreter + MLX | torch never imported |

## Goals

- On macOS arm64, bge-m3 (dense + sparse) and bge-reranker-v2-m3 run on MLX; torch, FlagEmbedding and
  sentence-transformers are never imported by `groundly start`, `groundly mcp`, `groundly serve`, the CLI's
  `search`, `import` re-embedding, or the indexing parent process.
- `groundly index` extracts every file of a run in one warm worker process instead of one process per file, keeping
  today's crash isolation and per-file timeout (UC-01 A2, A5).
- Same retrieval results: top-5 of `search` unchanged on the 50 spike queries; vectors within the tolerance below.

## Non-goals

- **Removing torch from the install.** docling's layout model and TableFormer need it, and the extraction worker
  keeps using them. Measured: torch-free extraction needs three patches to docling internals plus a `--no-deps`
  install `uv tool install` cannot express, runs PDFs ~1.2× slower, and loses TableFormer (table-heavy decks drop to
  word F1 0.84). Revisit if docling makes torch optional upstream.
- **Apple Vision OCR.** Inside docling the layout model sets the page time (27.5 vs 30.6 s on 30 pages), and Vision was
  worse on English slides (F1 0.917 vs 0.951) with no model pin. RapidOCR (decision 14) stays.
- New runtimes for Linux/Windows (llama.cpp, ONNX Runtime). They keep today's torch path; the backend seam below is
  where a future runtime plugs in.
- Packing for the torch path, fp16 for the torch reranker, docling.rs, an INT8 layout model.

## Anti-goals (a failure even if it works)

- torch imported in the long-running process on Apple Silicon (directly, or through a conversion step run in-process).
- A second answer-generation path, a network model call, or anything that breaks "no LLM, no key".
- Losing a failure cause: a file that crashes, times out, is too large or has no text must still end
  `extraction_failed` with the same specific message, and a model-load failure must still be retryable with no row.
- One hostile file taking the whole indexing run down.

## Constraints

- Architecture rules: `groundly/llm/` holds only the local embedder and reranker; lazy load, never at spawn;
  one shared instance per process; idle unload after `IDLE_UNLOAD_SECONDS`.
- The embedding pin (model + hf_revision + dim + dtype + normalization) is the interchange contract.
- Exact pins for model libraries, guarded by `.claude/hooks/guard-pins.sh`.
- `distribution.md` promises macOS, Linux and Windows, CPU-only included.

## Approaches considered

1. **A runtime per platform (chosen).** MLX on macOS arm64, today's torch/FlagEmbedding path everywhere else, chosen
   at install by platform markers and at run time by platform check. Fastest on the machines in use, keeps every
   platform working, and leaves one seam for future runtimes.
2. **Apple Silicon only.** Least code; breaks Linux and Windows until a new runtime exists, against `distribution.md`.
3. **Keep torch everywhere, MLX as an extra.** Speed without keeping torch out of the long-running process.

Within (1), Paul chose to keep torch in the install for docling and keep RapidOCR, after the torch-free spike above.

## Design

### 1. Embedder and reranker backends (`groundly/llm/`)

- `BgeM3Embedder` and `BgeReranker` keep their public surface: `encode(texts)`, `encode_stream(texts, batch_size)`,
  `compute_score(pairs)`, `shared_embedder()`, `shared_reranker()`, `LazyModel` lifecycle. Callers and tests do not
  change.
- Each picks its backend in `_build()`: MLX when `import mlx.core` succeeds (it is installed only on Apple Silicon
  running macOS 14 or later, see Dependencies), else the current FlagEmbedding code unchanged. A Mac where MLX is
  absent or fails to import falls back to torch rather than failing. No config setting (zero-config stays total).
- The MLX backend is the spike's forward (`bench/py/mlx_py.py`): XLM-R written against `mlx.core` (~40 lines, shared
  by both models), f16 weights, inputs packed end to end up to 1,024 tokens per pass under a block-diagonal mask,
  positions restarting per input. Tokenizing uses `tokenizers` (already installed) with each model's own
  `tokenizer.json`.
  - bge-m3: dense = normalized CLS; sparse = `relu(sparse_linear(hidden))`, special tokens dropped, max weight per
    token id, as FlagEmbedding does.
  - Reranker: CLS → `classifier.dense` → tanh → `classifier.out_proj`, raw logit as today.
- MLX's buffer cache is capped (`mx.set_cache_limit`, 1 GB in the spike; uncapped it grew to 10 GB and halved
  throughput). `LazyModel.unload()` calls `mx.clear_cache()` where it now calls `torch.mps.empty_cache()`.
- Each model serializes its own forward passes with a lock (the reranker already has one); FastMCP runs tools in a
  threadpool.

### 2. Weights

- The reranker pin ships `model.safetensors`; MLX loads it directly.
- The bge-m3 pin ships only `pytorch_model.bin` and `sparse_linear.pt` (torch pickles). On first use they are
  converted once to safetensors **in a child process** (`python -m groundly.llm.convert`, torch available because
  docling needs it, `torch.load(weights_only=True)`), written next to Groundly's other model state, and reused after.
  The serving process only ever reads the safetensors. `groundly models` (download/remove) covers the converted files.
- The conversion is deterministic from the pinned files; nothing new is downloaded.

### 3. The pin (Paul: unchanged)

MLX f16 vectors differ from torch MPS fp16 vectors by dense cosine ≥ 0.9997, sparse top-10 identical, rerank top-5
identical on all 50 queries, max score difference 0.026. The pin has never fixed the engine (torch on MPS, CUDA and
CPU already differ by amounts of this order), so **the pin is unchanged**: same model, revision, dim, dtype,
normalization; no manifest bump, no re-index, and bundles move between a Mac and a Linux machine as today. Decision
48 records the measured tolerance as part of the contract ("an engine change must stay within it"). Rejected: an
`engine` field in the pin, which would make every Mac↔Linux import re-embed through the existing `re_embed` path.
This supersedes the spike write-up's "the converted file becomes part of the pin".

**The topic gate (Paul: allow, measure, record).** The MLX reranker runs f16 where torch runs fp32 (`use_fp16=False`),
so scores move by up to 0.026, and `submit_tree` compares raw scores to `TOPIC_RELEVANCE_CUTOFF = 1.0`. A hit right at
the cutoff can land on the other side on a Mac. That is accepted: the gate shows topicality only, and torch already
differs by device. The plan measures it on the apd, bgf and sma trees (nodes whose accept/reject or source set
changes) and decision 48 records the count.

### 4. Dependencies

- `mlx==<exact>` with marker `sys_platform == 'darwin' and platform_machine == 'arm64' and platform_release >= '23'`
  (Darwin 23 = macOS 14, the oldest macOS mlx ships wheels for); added to `guard-pins.sh`. The spike used 0.32.2,
  PyPI's current is 0.32.4; the builder pins the current release after re-checking the tolerance. Older Apple Silicon
  macOS installs without mlx and runs the torch backend.
- FlagEmbedding, sentence-transformers and transformers stay unconditional (Linux/Windows backend, and docling's
  worker uses transformers' tokenizer).

### 5. Long-lived extraction worker (`groundly/ingestion/`)

- `SubprocessExtractor` keeps its interface (`extract(path, ocr_lang) -> Extraction`, raising `ExtractionFailure` /
  `ModelUnavailable`) and its controls: argv exec, no shell, temp working directory, output size cap, stderr to a file
  with only its tail read.
- The worker starts once per indexing run (lazily, on the first file that needs it) and handles files one at a time
  over a line protocol on stdin/stdout: a request names the input path, output JSON path and limits; a reply carries
  a status that maps 1:1 onto today's exit codes (`ok`, `no_text`, `model_unavailable`, `input_too_large`, `error`
  with its message). Large results still go through the output file, never the pipe.
- **Isolation stays per file.** If the worker crashes, the file in flight becomes `extraction_failed` with the parser
  failure message and a fresh worker starts for the next file. If a file exceeds `ingestion.timeout_seconds`, the
  parent kills the worker, records the timeout, and restarts.
- docling converters are built once per input format and OCR language and reused; `_first_frame`'s temp file and the
  notebook temp dir stay per file.
- The worker exits when the run ends (stdin closes) and is never kept between runs.

### 6. Docs (same change set, via `/decision`)

`groundly-spec.md` §4 item 4 and decision register (48), `tech-stack.md` (engine per platform, mlx pin, tolerance),
`architecture/overview.md` and `retrieval.md` where they name FlagEmbedding, `distribution.md` (Apple Silicon fast
path; footprint), UC-01 wording if the worker changes any observable behaviour (it should not).

## Acceptance (what "done" means)

1. On macOS arm64, after a `search`, `submit_tree` and an `import` re-embed in one `groundly start` process,
   `torch`, `FlagEmbedding`, `sentence_transformers` are absent from `sys.modules` (extends the existing
   `test_mcp_server.py` / `test_app.py` checks).
2. MLX vs torch on the apd store: dense cosine ≥ 0.9997 for every chunk, sparse top-10 identical, rerank top-5
   identical on the 50 workload queries (a recorded measurement; the workload lives in the spike repo). Topic-gate
   flips on apd, bgf and sma counted and recorded in decision 48.
3. Every existing test passes; Linux CI runs the torch backend unchanged.
4. `groundly index` on apd: one worker process for the run (observable in a test with a stub worker), same chunk
   count as before, wall time recorded.
5. A file that crashes the worker, one that times out, one over the size cap and one with no text each end
   `extraction_failed` with today's messages, and the files after them index normally (UC-01 A2, A5).
6. Idle unload on MLX returns memory (process RSS after `unload()` measured, like the idle-unload PR did for torch).

Where checks run (my call): CI stays as it is (ubuntu, torch backend; GitHub's hosted macOS runners are VMs without
usable Metal). Items 1 and 6 are tests marked to skip unless MLX is importable, run locally on Apple Silicon before
merge; items 2 and 4 are recorded measurements in the PR.

## Left to the builder

- Module layout inside `groundly/llm/` (one MLX module shared by both models is expected).
- Where the converted safetensors live and how `groundly models` lists them.
- The worker's exact message format and restart bookkeeping.
- Whether to recycle the worker after N files if its memory grows (measure first).
- Order of work: the worker and the MLX backend are independent and can land as separate PRs.
