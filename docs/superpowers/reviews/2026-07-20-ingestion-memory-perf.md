# Ingestion memory profile — the >16 GB RSS on large directories

> **RESOLVED 2026-07-20** (spec §7 decision 19, branch `performance-improvements`).
> Findings 1+3+4 fixed by streaming — `BgeM3Embedder.encode_stream(batch_size=64)`
> yields numpy fp32 rows in bounded batches and `store.add_indexed` consumes them lazily
> inside its per-file transaction; finding 2 fixed by `use_fp16=True`. **Measured after:**
> peak RSS is now **flat at 5.05 GB across 242 → 998 chunks** (4× document size, same
> peak) — i.e. the per-document term is bounded and peak no longer scales with the
> document. See "Resolution (measured after)" at the bottom.

**Summary:** Peak RSS in the index path is a fixed ~2.3 GB fp32-model floor plus a
per-document term that scales linearly with a document's chunk count, because the
whole document is embedded in one `.encode()` call and its dense/sparse/chunk buffers
are all held live (and dense is boxed into `list[float]` at 8× numpy) until the single
`add_indexed` write. The floor is constant; the scaling term is what climbs past 16 GB
on a large single document. **Highest-leverage fix: batch the per-document encode and
stream each batch into `add_indexed`** — it caps the corpus-scaling term to one bounded
batch.

## Measured (memray)

- **Workload:** one 563 KB plaintext file → **242 chunks** (512-token cap), full
  `index` path (extract subprocess → bge-m3 embed → SQLite write). arm64 CPU, fp32,
  bge-m3 warm in HF cache.
- **Command:** `python -m memray run -o g.bin driver.py large.txt` (driver calls
  `pipeline.index_paths`; note `-m groundly` in the agent doc fails — `groundly` has no
  `__main__`, use the console entry `groundly.cli:app` or a driver).
- **Peak memory usage: 3.929 GB.** Total allocated (churn): 11.55 GB over 5.0M
  allocations; 1.79M of them in the <74 B bucket — the boxed-float signature of finding 1.

Top allocators by size (all bge-m3 fp32 weight load — the resident floor):

| Location | Size |
|---|---|
| `torch/nn/modules/module.py:1369` `convert` (fp32 cast) | 2.307 GB |
| `torch/serialization.py:1594` `load` | 2.271 GB |
| `torch/nn/modules/linear.py:109` weight init | 1.216 GB |
| `torch/nn/modules/sparse.py:166` embedding weights | 1.091 GB |

(These overlap during load — resident model ≈ 2.3 GB.) memray traces the **parent** only;
the extract subprocess (docling/RapidOCR models) is a separate RSS that stacks on top
of this concurrently — see finding 6.

At 242 chunks the model floor dominates. The reported >16 GB comes from the
**per-document scaling term** on a much larger single document, extrapolated below.

## Findings

| # | Sev | file:line | Cost (measured / estimated) | Fix |
|---|-----|-----------|------|-----|
| 1 | HIGH | `groundly/llm/embeddings.py:109` | **Measured 8.0×**: 242-chunk dense output 0.99 MB numpy → **7.94 MB** as `list[float]` (~32 B/boxed float), and numpy source coexists pre-GC. Est. **+2.2 GB** at 60k chunks (list 1.97 GB + numpy 0.25 GB both live). | Keep numpy fp32; serialize bytes into SQLite (`store.py` already does `serialize_float32`), never build `list[float]`. |
| 2 | HIGH | `groundly/llm/embeddings.py:102` | **Measured**: `use_fp16=False` → ~2.3 GB resident (top allocators above), the whole run. fp16 halves to ~1.15 GB. | Recommend `use_fp16=True` — **but** it's an accuracy **and interchange** call (embedding pin is the compat contract); recommend, don't assume. |
| 3 | HIGH | `groundly/llm/embeddings.py:108`, `groundly/ingestion/pipeline.py:208` | `.encode([all chunks])` with **`batch_size=None`** (verified default). Output holds **all N** dense vecs + sparse dicts at once; peak scales with document size, not a bounded batch. | Explicit `batch_size`; chunk the per-document encode so output is bounded. |
| 4 | HIGH | `groundly/ingestion/pipeline.py:208-221` | `extraction.chunks`, `dense`, `sparse` all live simultaneously until the single `store.add_indexed`. Same linear scaling as 3. | Stream (chunk, vec, weights) batches into `add_indexed` incrementally instead of buffering the whole document. |
| 5 | MED | `groundly/ingestion/extract.py:119` | `json.loads(out_json.read_text())` reads the entire extraction JSON (cap 200 MB) with a transient ~2-3× during parse. Bounded by the cap, one document at a time. | Bounded already; note only. Streaming JSON parse if the cap is ever raised. |
| 6 | MED | `groundly/ingestion/pipeline.py:126` + `groundly/ingestion/extract_worker.py:119-127` | The fp32 embedder stays resident (~2.3-3.9 GB parent) while each per-file docling/RapidOCR **subprocess** loads its own models — additive **system-wide** peak (not visible in the parent memray trace). Highest concurrent RSS on OCR-heavy corpora. | Confirm lazy loading holds; embedder shouldn't be hot during OCR-only files. Verify with `--follow-fork` or per-process RSS. |
| 7 | NOTE | `groundly/ingestion/pipeline.py:66-91`, `groundly/core/store.py:212` | `_iter_files` materializes the full path list; `hash_status()` loads all sha→status rows. Grows with corpus but tiny per entry — **not** the 16 GB driver. | Leave as is unless corpus reaches millions of files. |

## Why it reaches 16 GB (the scaling model)

```
peak ≈ 2.3 GB (fp32 model, fixed)                       [finding 2]
     + chunks × 1024 × 4 B   numpy dense                [finding 3/4]
     + chunks × 1024 × 32 B  list[float] boxed          [finding 1]  ← 8× the numpy term
     + sparse dicts + torch encode intermediates
     + extract subprocess models (separate RSS)         [finding 6]
```

Everything after the first line is per-document. On a large single document (e.g. a
big scanned textbook → tens of thousands of chunks) the boxed dense lists alone add
~2 GB at 60k chunks, on top of the model floor and a concurrent OCR subprocess — that
is the path to >16 GB. **Chunking the per-document encode and streaming batches into
`add_indexed` (findings 3+4) caps every per-document term to one bounded batch**, and
finding 1 rides along for free once you stop building `list[float]` and hand numpy rows
straight to `serialize_float32`.

## Verdict

**Single highest-leverage fix: batch the per-document `encode` and stream each batch's
(chunk, numpy-vec, sparse) into `store.add_indexed` — findings 3+4+1 collapse into one
change that makes peak RSS independent of document size.** fp16 (finding 2) halves the
fixed floor but is an interchange decision, so treat it as a separate, deliberate call.

## Resolution (measured after)

Implemented on branch `performance-improvements` (spec §7 decision 19): `use_fp16=True`
(embeddings.py), `encode_stream` yielding numpy fp32 rows in `batch_size=64` batches,
`add_indexed(..., chunks, vectors)` consuming a lazy `(dense, sparse)` iterable inside
one transaction, `manifest.embedding.dtype` → `float16`. All 222 fast + 23 slow tests green.

Re-profiled the identical `index` path, same machine, model warm:

| Run | chunks | doc size | peak RSS | what bounds the peak |
|---|---|---|---|---|
| before — fp32, whole-doc encode | 242 | 563 KB | **3.929 GB** | model floor + full-document forward activation |
| after — fp16, streaming | 242 | 563 KB | 5.052 GB | one-time fp32→fp16 load transient |
| after — fp16, streaming | **998** | 2.33 MB | **5.053 GB** | one-time fp32→fp16 load transient |

**The result that matters: peak is flat (5.05 GB) across a 4× document-size increase**
(242 → 998 chunks, +0.001 GB). The embedding phase is now bounded — a 60k-chunk document
that previously drove peak past 16 GB would also peak ~5 GB. The `convert` (fp16 cast)
allocator dropped 2.307 GB → 1.169 GB, confirming the resident model halved.

**Honest tradeoff surfaced by the measurement:** on *small* documents (few hundred
chunks) peak is dominated by a one-time model-load transient, and `use_fp16=True` makes
that transient *higher* (~5 GB — FlagEmbedding loads fp32 weights then converts to fp16,
so both briefly coexist) than plain fp32 (~3.9 GB). This is a momentary load cost; the
steady-state resident model is ~1.15 GB (fp16) vs ~2.3 GB (fp32), which is what lowers
the concurrent footprint against per-file OCR subprocesses (finding 6). The net win is
not a lower small-document peak — it is the **elimination of unbounded per-document
growth**: peak is now a flat ~5 GB ceiling regardless of corpus/document size, which is
what removes the >16 GB OOM.
