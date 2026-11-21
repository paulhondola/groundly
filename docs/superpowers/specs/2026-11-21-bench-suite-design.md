# Bench suite design (2026-11-21)

## Intent

Two jobs, one tool.

1. **Settle the PDF router's speed gate now.** Decision 49 set "a fresh `groundly index apd`
   ≤ 190 s" from a spike. The built router ran 208 / 212 / 194 s against 237 s for `main`, but the
   same machine ran the spike itself at 178 s one session and 192 s the next. An absolute number on a
   noisy laptop cannot settle it; the router against `main`, interleaved in one session, can.
2. **Leave a baseline every future extraction stack is measured against.** A *stack* is a whole
   extraction pipeline: docling as on `main` (standard), the routed docling on `pdf-page-router`, and
   later docling slim on MLX, MinerU, PaddleOCR-VL and the like. Today only the standard bench types and
   the two existing stacks are built; adding a stack later is one file.

Paul is the only user. The output is numbers he trusts enough to sign off a gate and to cite in the
thesis.

## Goals

- One repeatable command per bench type, each writing result rows that `compare.py` can put side by
  side with the baseline.
- Accuracy split by page kind (text / mixed / image / blank) and by language (RO / EN), so a stack's
  wins and losses on Romanian OCR pages are visible, not averaged away.
- Peak memory of the **whole process tree** (the indexing parent plus its extraction worker), not just
  the parent.
- A verdict for decision 49's gate.

## Non-goals

- Not a CI job and not a pytest suite. Benches run by hand on the M1 Pro.
- No new stacks now (no MinerU, no MLX docling). Only `docling_standard` and `docling_routed`.
- No dashboards. A table printed in the terminal is the report.
- No re-index of every subject on `main` (approach C, rejected below).

## Anti-goals (a failure even if it runs)

- A stack scored against a reference it produced itself (the routed stack *is* the text layer on text
  pages, so a text-layer-only score always crowns it).
- A gate verdict from runs taken in different sessions.
- Course text leaking into git: result rows and the corpus manifest carry hashes, page numbers and
  scores, never page text. The hand-checked reference pages are the one exception and stay untracked
  (below).
- Bench code imported by `groundly/`, or a bench dependency in Groundly's default install.
- A bench dependency that changes any package the product already uses (above all docling, rapidocr,
  onnxruntime, opencv, torch, transformers, mlx): the baseline stacks would then measure a different
  Groundly.

## Constraints

- **Conventions conflict, resolved here:** `.claude/rules/conventions.md`, `CLAUDE.md` and
  `docs/groundly-spec.md` say "measurement protocols and results are kept outside the repo". Paul asked
  for `bench/` in this repo. Resolution: bench **code and the corpus manifest** are versioned in
  `bench/`; **results and reference text** are not (`bench/results/` and `bench/reference/` are
  gitignored, like `results-*.json` today). The convention line in those three files is amended in the
  same change set to say so.
- Course materials live in the untracked `data/` (and copies under `~/.groundly/<subject>/materials/`).
  The manifest refers to files by sha256 + a path hint; a missing or changed file fails loudly.
- `bench/` is outside the `groundly` package, so the import-linter layers do not apply. It may import
  `groundly` (the in-process stage profiler does); `groundly` never imports `bench`.
- Pinned deps are guarded (`.claude/hooks/guard-pins.sh`). `psutil` 7.2 and `memray` 1.20 are
  already installed. MinerU goes **into Groundly's own venv** (Paul, 2026-11-21: fine for current
  testing) as an optional `bench` extra in `pyproject.toml`, never in the default dependencies:
  `mineru-vl-utils[mlx]` exactly pinned. A dry run against today's venv adds 11 packages
  (`mineru-vl-utils` 2.0.9, `mlx-vlm` 0.7.12, …) and changes none, mlx 0.32.4 included, with one
  exception to suppress: it pulls `opencv-python` 5.1, which would overwrite the `cv2` of the installed
  `opencv-python-headless` 5.0 that RapidOCR uses. The extra excludes it (a `[tool.uv]` override, or
  whatever uv accepts), and the plan checks `cv2.__version__` and the lockfile diff before and after.
  If the lock changes any existing package, stop and report rather than accept it.
- Machine: M1 Pro, 16 GB, macOS 27.2. Every row records the machine so later runs on other hardware do
  not get compared silently.

## Approaches considered

| | Approach | Trade-off | Verdict |
|---|---|---|---|
| **A** | Paired end-to-end runs: `main` and the router alternate, 5 runs each, through the real `groundly index` CLI in fresh scratch homes, plus one run of each on two new subjects and the three files that timed out under the router | Settles the gate on the gate's own terms; ~35 min for apd, 2–3 h with the new subjects, unattended | **Picked** |
| B | Page sampling: a few hundred pages through each stack in one process, scaled up | 15 min, but the gate is an end-to-end CLI number and the ~30 s the router still loses sits in worker start-up and embedding, which sampling cannot see | Rejected for the gate; kept as the `extract` bench type for accuracy |
| C | Re-index every subject on `main` | Most complete, ~5+ h, adds little to the gate verdict | Rejected |

Accuracy reference: text layer plus a hand-checked set (picked) against text layer only (rejected: it
gives OCR pages no fair score, and OCR pages are exactly where future MLX/VLM stacks differ).

## The design

### Layout

```
bench/
  README.md        how to run each bench, what each metric means
  common/
    monitor.py     run a command or callable; sample the process tree's summed RSS (psutil, 100 ms);
                   wall time; optional --memray (memray run --follow-fork); returns one result dict
    corpus.py      load and verify manifest.json (sha256 of each file), page iteration
    results.py     append a row to bench/results/<type>.jsonl with the run metadata
    compare.py     table of any stack/ref against the baseline: medians, spread, ratio
  stacks/
    docling_standard.py   docling 2.115 + RapidOCR as Groundly on main runs it, per page
    docling_routed.py     the pdf_router path on pdf-page-router, per page
    mineru.py             MinerU2.5 on MLX (bench extra): drafts the reference pages
  extract/
    sample.py      build corpus/manifest.json from a census (stratified sample)
    run.py         run one stack over the corpus in its own interpreter, collect per-page JSONL
    score.py       word F1, diacritic-word recall, per page kind x language
  index/
    run.py         interleaved A/B of `groundly index` across git refs in fresh scratch homes
    stages.py      in-process run of IngestionPipeline using its on_event hook for extract/embed split
  retrieval/
    run.py         hit@8 exact page and within one page, MRR@8, against an indexed home
    queries/apd.json         the 50 labelled EN queries from the router spike
    queries/<ro-subject>.json  ~30 new labelled RO queries
  corpus/
    manifest.json  versioned: {sha256, path_hint, page, kind, lang} per sampled page
  reference/       gitignored: hand-checked text for the reference pages
  results/         gitignored: *.jsonl
```

### Stack contract

A stack is a script run as its own process, so a future stack whose dependencies do not fit Groundly's
venv can get its own interpreter (`uv run --with …` or a separate venv) without touching the harness.
Today all stacks run on Groundly's venv; `stacks/mineru.py` (MinerU2.5 on MLX, two-step, as in the
spike) is used to draft the reference pages and can be scored like any other stack. Input: the manifest path (and optionally a subset).
Output on stdout, one JSON line per page:

```json
{"sha256": "…", "page": 12, "text": "…", "ms": 184.2, "error": null}
```

`docling_routed` reproduces the real pipeline, including `pdf_router`'s `is_slide_deck()` check: a
non-deck PDF goes to docling whole, exactly as on `main`, so its pages score the same for both stacks.
The sample is therefore stratified by deck / non-deck as well, and the "it is the text layer" caveat
applies to deck text pages only. *(Builder-check call.)*

OCR runs with RapidOCR's default models (no `--ocr-lang`), which is how every indexed subject was
built (`ocr.lang: []`) and how the spike measured; result rows record the OCR language so a later
`--ocr-lang` run is never compared silently. *(Builder-check call.)*

The harness wraps the process in `monitor.py` for wall time and peak memory, and never imports the
stack. Each stack warms up on one untimed page first. Page text goes into a temp file the scorer reads,
never into `results/`.

### Corpus

A census classifies every PDF page of the chosen subjects (the router's own `classify_page`, so kinds
match what the router sees) and tags language per page: RO when the page has Romanian diacritics or the
document's majority does; EN otherwise. A stratified sample is drawn across kind × language, about
200 pages in total, including:

- about 40 **reference pages**, mixed and image pages, half RO and half EN, for the hand-checked set;
- the rest as text and blank pages, scored against the text layer.

The manifest is frozen once written. Changing it is a new manifest version, and results record which
version they used.

### Accuracy reference

- **Text pages:** the PDF text layer (pypdfium2), with the anti-goal noted in the report: the routed
  stack reads this layer directly, so its text-page score shows what other stacks *drop or add*, not a
  win for routing.
- **Reference pages (~40):** Claude drafts each page's text from MinerU's output (`stacks/mineru.py`
  run over the reference pages; the earlier spike's MinerU output covers other pages and is not reused) (the best Romanian
  reader measured, F1 0.998 on sma), Paul corrects it once (about an hour), and it is saved under
  `bench/reference/<sha256>-<page>.txt`. From then on every stack is scored on the same truth.
- **Metrics:** word F1 (order-insensitive, words longer than 2 characters, NFC-normalized), bigram F1,
  and diacritic-word recall (words containing ă â î ș ț or the cedilla forms ş ţ, counted separately so
  a stack that normalizes comma-below to cedilla shows up). Reported per kind × language.

### Index bench and the gate

- Refs: `main` and `pdf-page-router` checked out side by side as git worktrees, each with its own
  `uv sync`'d venv, so neither run imports the other's code.
- Order: main, router, main, router, … 5 runs each on apd, each into a fresh scratch `GROUNDLY_HOME`,
  so no run inherits an index. Each scratch home symlinks `models/` to `~/.groundly/models` (the
  converted bge-m3 safetensors) and shares the HF cache, as the spike's homes did: the one-time weight
  conversion is not part of the gate. *(Builder-check call.)*
- Input: the 187 files in `~/.groundly/apd/materials/` (what the spike and the 237 s / 208 s runs
  indexed: 187 materials, ~1,200–1,340 chunks), not `data/Algoritmi-Paraleli-si-Distribuiti/`
  (282 files, unlike anything measured). Default `config.toml` (timeout 300 s, rerank on), same as
  the live home. *(Builder-check call.)*
- Per run: CLI wall time and the summed peak memory of the process tree from `monitor.py`; chunk count and
  failed files read from the resulting `store.db`. `stages.py` runs once per ref for the
  extract / embed split (not part of the gate).
- **Gate verdict:** the router's median wall time against `main`'s median in the same session, with
  the spread. The spike's measured win was 240 s → 178 s (1.35×). The proposed restated gate is
  **router median ≤ 0.85 × main median, same session**, which is the original 190/237 ratio rounded.
  Whether to restate decision 49's gate this way is Paul's call at the result, recorded through
  `/decision`.
- Then one run of each ref on two new subjects, picked by the census as the most Romanian one and the
  most English one among bia, gac, lftc, pdss, pi, pm and romsilva (excluding pi and bia on size), plus
  the three files that timed out under the router (`LFTC Cursuri.pdf`, `PI_Slide-uri_3.pdf`,
  `NT1 - Compozitii, scheme si tehnologii de regenerare.pdf`) indexed alone on each ref. Each timeout
  is reported as router-caused, shared, or neither.

### Retrieval bench

Runs the labelled queries through Groundly's `search` against an index each ref produced. Reports
hit@8 on the exact page and within one page, plus MRR@8, per query set. The apd set already exists
(50 EN queries, `{query, file, page}`) at
`~/Developer/groundly-rs/bench/extract-alt/route/queries_labeled.json` and is copied into
`bench/retrieval/queries/apd.json` unchanged, so its numbers stay comparable with the spike's; Claude writes about 30 RO queries on the most-Romanian new subject in the same style
(query, file, page) and Paul spot-checks them.

### Result rows

Every row: bench type, stack or git ref + sha, manifest version, engine versions (docling, rapidocr,
pypdfium2, mlx), machine, date, run index, and the metrics. `compare.py` groups by stack/ref and prints
medians, spread and the ratio to a chosen baseline.

## What is left to the builder

- How `monitor.py` samples (interval, how it finds children) as long as it sums the tree.
- How `docling_standard.py` reproduces main's per-page output: calling docling the way `main`'s
  extract worker does and splitting by page is fine.
- Exact census thresholds come from `pdf_router.py` constants, not new ones.
- The RO-query writing and reference-draft prompts.
- README wording.
