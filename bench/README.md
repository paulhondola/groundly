# bench/

Measurements of Groundly's extraction stacks. Bench code and the page manifest live here; results (`bench/results/`) and reference text (`bench/reference/`) stay out of git.

The embedder and reranker are fixed (bge-m3 on MLX). What varies is the **stack**: the reader that turns a file's pages into documents.

## Stacks

One module per stack in `bench/stacks/`. A stack is a **reader**. It declares:

- `SUFFIXES`: the file formats it claims. Every other file takes Groundly's own path.
- `read(path, pages, ocr_lang)`: yields `(page_no, one-page DoclingDocument)` for every requested page (every page when `pages` is `None`), in ascending order, the same contract as the product's `extract_worker.docling_pages`. A VLM reader calls `caps.hit(n)` for a page whose output stopped at its token cap.

Each module ends with `_worker.main(stack)`, which builds two modes from `read`:

- `python -m bench.stacks.<name> --serve` speaks Groundly's extraction-worker protocol: a claimed file goes through the product's `chunk_pages`, so chunking, heading carry and page citations are the product's own. The index bench plugs it into the real `IngestionPipeline` via `SubprocessExtractor(argv=…)`, so the product has no stack flag.
- `python -m bench.stacks.<name> --pages MANIFEST --out OUT` writes one JSONL row per page (`text`, `ms`, `error`, `capped`) and a final `meta` row with `load_s`. Pages are read in one timed call per (file, kind); a page's `ms` is that call's time over its page count, after one untimed warm-up page that carries the model load. A standalone image is read as its first frame, as the product does.

| Stack | What it is |
|---|---|
| `docling` | docling + RapidOCR page by page, as the product reads (`extract_worker.docling_pages`) |
| `mineru` | MinerU2.5 two-step on MLX, each page rendered at scale 2, its blocks joined as Markdown and turned into a one-page document (`markdown_page`); table HTML becomes a pipe table. Image and chart blocks give no text (MinerU's image analysis is off: a generated description is not text on the page). Token cap 8192 |
| `granite` | granite-docling 258M on MLX through docling's `VlmPipeline`, in the product's 16-page batches. Token cap 8192 |
| `docling_mineru` | `by_kind`: pages that classify `image` go to `mineru`, every other page to `docling` |
| `docling_granite` | the same with `granite` |
| `text_layer` | not a reader: pypdfium2 text-layer reading and page classification for the corpus census and `by_kind` |
| `product` (index arm only) | the shipped worker (`groundly.ingestion.extract_worker`) |

Set the bench up with `uv sync --extra dev --group bench` (the VLM stacks need the group). `docling_standard`, `docling_routed` and `docling_paged` were retired; their rows stay in the pre-store `index.jsonl` archive (`python -m bench.common.store import-jsonl` loads it once).

To add a stack, write one module with `SUFFIXES`, `read` and the `_worker.main` footer. Nothing in `groundly/` changes, and `groundly/` never imports `bench`.

## Benches

Every run is written to the DuckDB store `bench/results/bench.duckdb` (gitignored) in one transaction: a `run` row, its child rows, and its samplers' time series. Rows are raw facts; scores per cell, energy and CPU % are queries. A `session` row records the machine, OS, git sha and package versions.

| Table | One row per |
|---|---|
| `run` | measured run: bench, arm, subject, run number, start/end, return code, `wall_s`, `load_s`, `items`, `memray`, `idle_combined_mw`, `power_sampler`, `sampler_note` |
| `page`, `page_text` | extracted page: time, cap, error, scores with `scorer_version` / its text (course text: never exported) |
| `file` | file of an index run: `extract_s`, `embed_s`, status, error |
| `query`, `query_hit` | labelled query: latency, hit within one page in the top 8 / each ranked hit |
| `sample_proc` | 0.1 s tick: tree RSS, physical footprint, CPU seconds, system GPU memory (once a second) |
| `sample_power`, `sample_task` | powermetrics 0.5 s sample: CPU/GPU/ANE/combined mW, GPU active and MHz / per process of the run's tree: CPU ms/s, energy impact |
| `artifact` | memray capture or flamegraph, with its peak heap |

Every bench takes `--runs` (index 5, extract and retrieval 1) and `--no-power`. Without it, one `sudo -n powermetrics` runs for the whole invocation, after a 10 s idle baseline; if sudo would prompt, the bench stops with `powermetrics needs sudo — run "sudo -v" first, or pass --no-power`. Per-process GPU time is NULL on macOS versions whose powermetrics does not report it.

`compare` reads the store and only compares runs from **one session on one machine**, never memray runs. Runs with a nonzero return code are dropped and counted. `python -m bench.extract.score rescore [--all]` recomputes stored page scores after a scorer change (or, with `--all`, a corrected reference).

### index: end-to-end speed and memory

```bash
uv run python -m bench.index.run --stacks docling product --runs 5 [--timeout 3600] \
    --subject apd --input ~/.groundly/apd/materials [--keep-homes] [--memray] [--no-power]
uv run python -m bench.common.compare index --baseline docling --gate 0.85
```

The stacks run interleaved. Each run is a fresh child process with its own scratch `GROUNDLY_HOME`; `models/` is symlinked to `~/.groundly/models`.

Each run is a `run` row (`items` = chunks in `store.db`) with one `file` row per file: `extract_s` and `embed_s` from the pipeline's stage events (a failed file's time counts as extract), and failed files with their message (a timeout's says so).

**Gate:** a stack passes when its median ≤ gate × the baseline's median, both from the same session. A ratio is used because absolute seconds drift with machine load.

**memray caveat:** `--memray` traces both processes, the run's parent under `memray run --follow-fork` and the worker under its own `memray run`, which slows them down. Each capture and its flamegraph is an `artifact` row with its peak heap, in `bench/results/memray/`. Those runs are flagged `memray` and never used for timing. Psutil RSS counts shared copy-on-write pages once per process, and it can miss spikes shorter than one sampling interval.

### extract: per-page accuracy

```bash
uv run python -m bench.extract.sample --version 2          # v1 manifest -> v2, renders pages
uv run python -m bench.extract.drafts                      # both engines' drafts + page images for the refs
uv run python -m bench.extract.run --stack docling [--only-ref] [--session S] [--runs N] [--no-power]
```

The manifest is a frozen sample, pinned by sha256; loading it refuses a missing or changed file. Version 2 keeps version 1's PDF pages and adds two sources. Every row names its `source`:

- `pdf`: a PDF page, `kind` `text`, `blank`, `mixed` or `image`. `text` pages score against the PDF's text layer. **`mixed` pages also score against the text layer, which misses text inside figures, so their scores are a lower bound.** A non-reference `image` page has no reference and is skipped.
- `image`: a standalone image from the materials, `kind` `standalone`, language tagged from docling's OCR. Images enter only as reference pages.
- `rendered`: about 50 `text` pages per language, rendered once at scale 2 (144 DPI) to the gitignored `bench/reference/rendered/`. Every engine reads the same PNG as a standalone image, and it scores against its source page's text layer (`render_of`). The row records `render_scale`.

**Reference pages** (`ref: true`): up to 10 per {scanned PDF page (`image`), standalone image} × {ro, en}. A cell with fewer candidates takes what exists, and the manifest's `short_cells` names it. Their hand-checked text lives in `bench/reference/<sha256>-<page>.txt`: Claude merges the docling and MinerU drafts against the page image, then Paul corrects each beside its image. A reference page with no checked text is skipped. Drafts, images and references are never committed.

| Metric (per `kind/lang`) | Meaning |
|---|---|
| `word_f1`, `bigram_f1` | multiset token overlap with the reference |
| `word_precision`, `word_recall` | the two halves of `word_f1`: words the stack invented vs. words it missed |
| `diacritic_recall` | share of the reference's ă â î ș ț the stack reproduced |
| `diacritic_recall_folded` | the same, with cedilla ş ţ counted as comma ș ț |
| `pages/…`, `diacritic_pages/…` | the n behind each average |
| `failed_pages`, `scored_pages`, `skipped_pages` | coverage; a failed page scores 0 |

The per-cell means above are printed after each run and computed by `compare` from `page` rows. Per run: `load_s` (the untimed warm-up page: model load plus one page), each page's `ms` and `capped` (VLM output stopped at the token cap). The printed summary also gives `model_disk_mb`, the arm's model weights on disk.

To compare stacks, pass the session id the first run prints to the next run's `--session`.

### retrieval: does it still find the right page

```bash
uv run python -m bench.retrieval.run --home <home> --subject apd \
    --queries bench/retrieval/queries/apd.json --arm product [--session S] [--runs N] [--no-power]
uv run python -m bench.common.compare retrieval --baseline docling --metric hit_within1@8
```

The bench runs labelled queries against an indexed home, such as one left behind by `index --keep-homes`, in a measured child: one warm-up search (`load_s`) loads the models, then each query is timed. It prints `hit_exact@8`, `hit_within1@8` and `mrr_within1@8`, and stores `query` and `query_hit` rows.

## Report: thesis results R1–R15

```bash
uv run python -m bench.report.run [--session S]
```

Writes `bench/results/export/R<n>.parquet`, a `.csv` twin and a `.png` figure for each result, from each bench's latest session (or one `--session`). The queries are named blocks in `bench/report/queries.sql`; R1's 95% CIs are a percentile bootstrap of the page mean. Energy is the trapezoid integral of combined power over the run's samples minus its idle baseline × wall time; CPU % is Δcpu_s / Δt_s. Accuracy results (R1–R3) use each arm's first run, since extraction is deterministic. memray runs feed only R15. No export reads `page_text`.

| Result | What |
|---|---|
| R1–R3 | word F1 / precision / recall per engine × cell with CIs; per-page F1 spread; cost (s/page, J/page) vs F1 |
| R4–R7 | per-page latency and model load; memory over time and peaks; CPU, GPU, power and energy per item; capped pages, failed items, timeouts |
| R8–R9 | index time per subject and arm with its extract / embed / other split; retrieval hit@8 |
| R10–R14 | torch vs MLX: throughput and load, `search` p50/p95, resources and J per 1,000 chunks, agreement, hit@8 |
| R15 | memray peak heap per process (flamegraphs beside the captures) |

## Ship rule

Written before any VLM run. A stack ("docling, with the VLM on image pages") replaces the product's reader only if all of these hold in one session:

- (a) On the hand-checked pages (scanned PDF pages and standalone images), `word_f1` is at least 0.05 higher than docling's in both languages, and `word_precision` does not drop.
- (b) `hit_within1@8` does not drop on apd or pm.
- (c) apd indexes in at most 1.5× docling's median time, and no file that docling indexes in apd, pm or the three files that timed out (`LFTC Cursuri.pdf`, `PI_Slide-uri_3.pdf`, `NT1 - …`) fails.
- (d) The engine has a torch/transformers path off Apple Silicon.

Passing still needs a `/decision`.
