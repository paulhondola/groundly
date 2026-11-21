# bench/

Measurements of Groundly's extraction stacks. Bench code and the page manifest live here; results (`bench/results/`) and reference text (`bench/reference/`) stay out of git.

The embedder and reranker are fixed (bge-m3 on MLX). What varies is the **stack**: the OCR model and routing that turn a file into chunks.

## Stacks

One module per stack in `bench/stacks/`. A stack declares:

- `SUFFIXES`: the file formats it claims. Every other file takes Groundly's own path.
- `extract(path, ocr_lang) -> dict`: the worker's result shape (`chunks`, …).
- `page_texts(path, pages, ocr_lang)`: the text of each page, for accuracy scoring.

Each module ends with `_worker.main(stack)`, which gives it two modes:

- `python -m bench.stacks.<name> --serve` speaks Groundly's extraction-worker protocol. The index bench plugs it into the real `IngestionPipeline` via `SubprocessExtractor(argv=…)`, so the product has no stack flag.
- `python -m bench.stacks.<name> --pages MANIFEST OUT` writes the per-page text as JSONL.

| Stack | What it is |
|---|---|
| `docling_standard` | docling + RapidOCR on every page |
| `docling_routed` | the pdf_router: text pages through pypdfium2, image and mixed pages through docling |
| `mineru` | MinerU2.5 on MLX. Needs the optional extra: `uv sync --extra bench` |

To add a stack, write one module with the three names above. Nothing in `groundly/` changes, and `groundly/` never imports `bench`.

## Benches

Every run appends one row per arm and run to `bench/results/<bench>.jsonl`. Each row records the session, arm, git sha, machine, versions, OCR language, manifest version, return code and metrics. `compare` only compares rows from **one session on one machine**. Runs with a nonzero return code are dropped and counted.

### index: end-to-end speed and memory

```bash
uv run python -m bench.index.run --stacks docling_standard docling_routed --runs 5 \
    --subject apd --input ~/.groundly/apd/materials [--keep-homes] [--memray]
uv run python -m bench.common.compare index --baseline docling_standard --gate 0.85
```

The stacks run interleaved. Each run is a fresh child process with its own scratch `GROUNDLY_HOME`; `models/` is symlinked to `~/.groundly/models`.

| Metric | Meaning |
|---|---|
| `wall_s` | whole run, model load included |
| `pipeline_s` / `extract_s` / `embed_s` | pipeline time and its split; failed files count toward `extract_s` |
| `peak_rss_mb` | peak RSS summed over the process tree, sampled every 0.1 s (psutil) |
| `chunks`, `materials`, `failed_count` | what landed in `store.db` |

**Gate:** a stack passes when its median ≤ gate × the baseline's median, both from the same session. A ratio is used because absolute seconds drift with machine load.

**memray caveat:** `--memray` wraps the worker in `memray run`, which slows it down. Its rows are flagged `memray: true`. Read them for the allocation profiles in `bench/results/memray/` and never for timing. Psutil RSS counts shared copy-on-write pages once per process, and it can miss spikes shorter than one sampling interval.

### extract: per-page accuracy

```bash
uv run python -m bench.extract.sample --version 1          # build bench/corpus/manifest.json
uv run python -m bench.extract.run --stack docling_routed --only-ref [--session S]
```

The manifest is a frozen, stratified sample of pages: kind (text, image, mixed) × language (ro, en). Files are pinned by sha256, and loading the manifest refuses a missing or changed file. About 40 pages are reference pages. Their hand-checked text lives in `bench/reference/<sha256>-<page>.txt`, and pages with no checked text are skipped.

| Metric (per `kind/lang`) | Meaning |
|---|---|
| `word_f1`, `bigram_f1` | token overlap with the reference |
| `diacritic_recall` | share of the reference's ă â î ș ț the stack reproduced |
| `diacritic_recall_folded` | the same, with cedilla ş ţ counted as comma ș ț |
| `pages/…`, `diacritic_pages/…` | the n behind each average |
| `failed_pages`, `scored_pages`, `skipped_pages` | coverage; a failed page scores 0 |

To compare stacks, pass the session id the first run prints to the next run's `--session`.

### retrieval: does it still find the right page

```bash
uv run python -m bench.retrieval.run --home <home> --subject apd \
    --queries bench/retrieval/queries/apd.json --arm docling_routed [--session S]
uv run python -m bench.common.compare retrieval --baseline docling_standard --metric hit_within1@8
```

The bench runs labelled queries against an indexed home, such as one left behind by `index --keep-homes`. It reports `hit_exact@8`, `hit_within1@8` and `mrr_within1@8`.
