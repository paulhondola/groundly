# Two-subject retrieval comparison — apd and passc (2026-08-09)

Measured on both pilot subjects with the same harness (decision 27), the same arms, and
set-size-matched cutoffs. Every number is measured; the two marked *(derived)* are exact
replays of shipped code against the parquet artifacts, not estimates.

**One line:** on the corpus built to favour graph retrieval, `hybrid-local` draws level
with the vector baseline on hit rate and still loses recall and MRR at every cutoff — and
nothing on either subject is statistically resolvable.

## What was measured

| | apd | passc |
|---|---|---|
| subject | Parallel & Distributed Algorithms | Software Architecture (PASSC) |
| materials | 187 (PDF, `.c`, `.java`, `.py`, `.md`, images) | 15 (PDF + 2 markdown) |
| chunks | 1,193 | 341 |
| gold questions | 48 | **76** |
| class mix (factoid / multi-hop / global) | 17 / 22 / 9 | 23 / 44 / 9 |
| multi-hop share | 46% | **58%** |
| language (EN / RO) | 39 / 9 | 66 / 10 |
| gold chunks per question (median) | 2 | 3 |

Arms: `vector` and `hybrid-local`, both **provider-free** (`_AbortAfterContext`), rerank on,
cutoffs 1/5/8/10/20, 0 errors on both subjects, neither run partial.

**passc was chosen to suit the graph arm.** It carries 58% multi-hop questions against
apd's 46%, and two items (`passc-051`, `passc-073`) require joining `Reflection.pdf` to a
persistence deck that shares no vocabulary with it — a shape apd's gold set has no
equivalent for. That matters for reading the result below: this is the graph arm's home
ground, not a corpus picked to embarrass it.

## 1. Retrieval quality, matched cutoffs

apd is on its `gpt-oss-120b @ gleanings=0` graph — the shipped build, and the best of the
three measured (see §4).

**Hit rate**

| k | apd hybrid | apd vector | passc hybrid | passc vector |
|---|---|---|---|---|
| 1 | **0.208** | 0.146 | 0.434 | **0.461** |
| 5 | 0.479 | **0.604** | **0.855** | **0.855** |
| 8 | 0.646 | **0.708** | **0.947** | **0.947** |
| 10 | **0.708** | **0.708** | 0.947 | **0.974** |
| 20 | 0.833 | **0.896** | **0.987** | 0.974 |

**Recall**

| k | apd hybrid | apd vector | passc hybrid | passc vector |
|---|---|---|---|---|
| 1 | **0.104** | 0.090 | 0.202 | **0.252** |
| 8 | 0.352 | **0.493** | 0.622 | **0.658** |
| 20 | 0.608 | **0.644** | **0.839** | 0.803 |

**MRR**

| k | apd hybrid | apd vector | passc hybrid | passc vector |
|---|---|---|---|---|
| 1 | **0.208** | 0.146 | 0.434 | **0.461** |
| 8 | 0.321 | **0.340** | 0.604 | **0.635** |
| 20 | 0.337 | **0.354** | 0.607 | **0.638** |

Three things read off this:

- **The baseline is never behind on MRR at k ≥ 5, on either subject.** Rank quality is the
  one axis where the result is consistent across corpora, and it favours `vector`.
- **On passc the arms tie on hit rate** — identical at k=5 and k=8 — while `vector` keeps
  the recall and MRR lead. On apd `vector` leads hit as well.
- **`hybrid-local` wins rank 1 on apd (0.208 vs 0.146) and loses it on passc (0.434 vs
  0.461).** The direction is corpus-dependent, so neither reading generalises.

## 2. Nothing is statistically resolvable

Exact two-sided McNemar against `vector`, at each matched cutoff.

| k | apd (n=48) | passc (n=76) |
|---|---|---|
| 1 | 8 vs 5, p = 0.581 | 10 vs 12, p = 0.832 |
| 5 | 4 vs 10, p = 0.180 | 5 vs 5, p = 1.000 |
| 8 | 3 vs 6, p = 0.508 | **1 vs 1, p = 1.000** |
| 10 | 4 vs 4, p = 1.000 | 0 vs 2, p = 0.500 |
| 20 | 0 vs 3, p = 0.250 | 1 vs 0, p = 1.000 |

Ten tests, none below p = 0.18. At `context_k = 8` — the cutoff the product actually uses
— passc's discordant split is **1 question against 1**.

The honest claim is therefore *"the baseline leads consistently in direction and neither
gold set can certify the size of the gap"*, not *"graph fusion loses by X"*.

**What these gold sets can resolve, exactly.** The harness runs an exact two-sided
McNemar, so with `d` discordant pairs all pointing one way `p = 2^(1-d)`: **d ≥ 6 clears
p < 0.05** (6-0 gives 0.031, 5-0 gives 0.063), and with a single dissenting question it
takes 8-1, i.e. d = 9 (0.039). **That threshold is a property of the split, not of n** — an
earlier draft of this document said n = 76 needs ~6 and n = 48 needs ~9, which wrongly
implied it scales with the gold set. What n actually changes is the *effect size* those
six questions represent: 6/48 = **12.5 pp** on apd against 6/76 = **7.9 pp** on passc. The
larger gold set buys a finer resolvable gap, not a lower discordant count.

> A separate run over three apd graph builds produced one cell at p = 0.031 (gleanings=1,
> k=20, 0 vs 6, favouring the baseline). With 15 tests at α = 0.05 expecting ~0.75 false
> positives, that is read descriptively too.

## 3. Cost and time

| | apd | passc |
|---|---|---|
| graph build | **$0.4880** | **$0.1524** |
| pipeline time (`stats.json`) | 2,618.4 s (0.727 h) | 890.9 s (0.247 h) |
| end-to-end (CLI) | 2,951.5 s | 926.0 s |
| extraction-model probe | 333.1 s | 35.1 s |
| tokens (prompt / completion) | 4.02 M / 2.00 M | 1.28 M / 0.62 M |
| cost per chunk | $0.000409 | $0.000447 |
| chunks failed / reports failed | 0 / 0 | 0 / 0 |

Both time rows are given because they are not interchangeable: the pre-pipeline probe does
not scale with corpus size, so quoting passc's end-to-end figure against apd's pipeline
figure — as an earlier draft of this document did — inflates passc's per-chunk time by
about 4%. The pipeline row is the one comparable across subjects.

Per arm, per query:

| arm | build cost | query cost | provider |
|---|---|---|---|
| `vector` | **$0** | **$0** | none |
| `hybrid-local` | one graph build | **$0** | none |
| `graph-global` | one graph build | ~56 map calls (~$0.017 on apd) | required |

`hybrid-local` being free per query is the strongest thing about it: since
`_AbortAfterContext` the arm never reaches a completion model, so the whole cost is the
one-off build. The comparison is therefore *"$0.15–0.49 once, then free, for no measurable
retrieval gain"* — not an ongoing expense.

**Latency is deliberately absent.** Every sweep here ran two arms together, so
`latency_comparable` is false in all of them (a resident model taxes the other arm — 5.4×
measured). Six single-arm runs would be needed; none were estimated.

## 4. The extraction model is the graph arm's ceiling

apd's graph was built three times with corpus, chunking, prompt and entity types held
identical — a controlled 2×2 minus a corner.

| | gemma-12b @ glean0 | gpt-oss @ glean0 | gpt-oss @ glean1 |
|---|---|---|---|
| entities | 2,685 | 3,704 | 6,184 |
| hit@8 | 0.604 | 0.646 | **0.667** |
| MRR@8 | 0.262 | 0.321 | **0.335** |
| hit@1 | 0.083 | **0.208** | **0.208** |
| build | 15.02 h, unpriced | **0.73 h, $0.49** | 1.28 h, $0.92 |

The first published comparison used the 12B graph and **understated the arm badly**: the
hit@8 gap to `vector` closes from 10.4 points to 4.1, and MRR@8 reaches 98.5% of the
baseline. A better graph alone beats the session probe that added a cross-encoder and
widened anchoring (56.2% hit@8) — the arm's ceiling was graph quality more than the
missing reranker.

**Gleaning does not pay for itself.** `gleanings` 0→1 costs 1.89× for 1.67× more entities
and *loses* deep recall (hit@20 0.833 → 0.771). It also does not explain the graph's
dangling nodes: isolated-entity share is 18.68% at 0 against 18.61% at 1. That rate is a
property of the extraction model (gemma 4.84%, gpt-oss 18.68%).

**Two build-cost claims made during this work are retracted.** The 15.02 h → 0.73 h
speedup is overwhelmingly *concurrency*, not model speed — `concurrent_requests()`
serializes loopback providers, so the local build ran 1 call in flight against 25
(measured `compute_duration / wall` 1.20× against 16.09×). And a "$0.15–0.70" projection
scaled from the 12B token count was 1.9× low against the $0.9202 actually spent.

## 5. The two graphs differ in a way that predicts the result

| | apd | passc |
|---|---|---|
| entities per chunk | 3.10 | **5.25** |
| relationships per entity | **1.79** | 1.15 |
| mean degree | **3.39** | 2.25 |
| isolated (degree 0) | 18.7% | **24.2%** |
| CONCEPT-typed | 47.5% | 54.4% |

passc extracts *more* entities per chunk and connects them *less* — the signature of
conceptual slide prose, which names many ideas per slide and states few relations between
them, against apd's code and algorithm listings where a chunk names a tightly coupled
cluster. Local search traverses relationships, so passc's question mix favours the graph
arm while passc's graph structure works against it. That the two roughly cancel is the
most economical explanation for the tie, and it is a hypothesis this data cannot separate
from the corpus simply being easier (both arms score far higher on passc).

## 6. `graph-global` is a corpus constant on both subjects *(derived)*

| | reports at level ≤ 2 | chunks returned | share of corpus |
|---|---|---|---|
| apd | 677 | 1,148 | **96.2%** |
| passc | 315 | 302 | **88.6%** |

Identical for every question, in ascending rowid order, so the arm has no rank at all and
is excluded from matched cutoffs and from significance (`UNRANKED_ARMS`). On apd the
constant *grows* as the graph improves (1,138 → 1,148 → 1,168 across the three builds).

Derived by replaying `GraphGlobalRetriever`'s citation join offline against the parquet —
deterministic, zero provider calls. The join key is the `community` column, not the report
`id` (a hash; joining on it returns the empty set). The one assumption is that global
search admits every report at level ≤ 2, which is what `dynamic_community_selection=False`
does. A live sweep would confirm it at ~$2.50 and was judged not worth it.

## 7. Contamination is concentrated at rank 1 on both subjects

Question-source files are themselves indexed, so an arm can retrieve the *question* rather
than the material answering it. `expected` labels may never name such a file (rejected at
load), and leakage is reported against the corpus base rate.

| | base rate | `vector` leakage @1 | enrichment | @8 | @20 |
|---|---|---|---|---|---|
| apd | 3.77% | 0.562 | **14.9×** | 3.6× | 2.5× |
| passc | 1.47% | 0.461 | **31.4×** | 7.3× | 5.9× |

On both subjects roughly **half of all rank-1 chunks are question-source material**. This
does not affect the arm comparison — both arms face it, and `hybrid-local` is consistently
*less* contaminated at k=1 (7.2× on apd, 19.7× on passc) — but it means the absolute hit
rates on both subjects are optimistic, and the thesis must say so. It is also the argument
for a contamination-controlled re-index.

## What this establishes, and what it does not

**Establishes.** Across two subjects, two corpus types, 124 gold questions and three graph
builds, fusing a graph arm into the vector baseline does not improve retrieval on the
metrics the product uses. The baseline's MRR lead is the one direction consistent
everywhere. The cost of finding this out is one graph build per subject ($0.15–0.49), and
`hybrid-local` is free per query thereafter.

**Does not establish.** That graph retrieval cannot work — arm 4 (`retrieval/adaptive.py`)
is still a stub, so this is three architectures, not four; the graph arm has no relevance
model of its own (`1.0 / (rank + 1)` over graphrag's context-assembly order) and anchors on
`top_k_entities = 10`, which on apd's improved graph is 0.16% of entities; and RRF at
reduced weight was probed but never landed in shipped code. Nor does it establish a *size*
for the gap: no cutoff on either subject reaches significance.

## Open

- Per-arm latency needs six single-arm runs; none of the numbers above carry a time axis.
- Arm 4 remains unimplemented.
- `max_cluster_size` is still unset (graphrag default 10); apd builds 360 reports at levels
  3–4 that `COMMUNITY_LEVEL = 2` never reads.
- A contamination-controlled re-index (exam files excluded from the corpus) would give
  honest absolute hit rates to sit beside these relative ones.
