# GraphRAG performance review — apd (2026-08-08)

Opus 5 performance-specialist agent, measured on Paul's machine against the 48-question
apd gold set. Every number is measured unless marked *(estimated)*; ones requiring a
provider are marked *(provider)*. Spot-checked independently — see "Verification" below.

**One line:** hybrid-local's 226s is 78–89% a graphrag synthesis call whose answer
Groundly discards; deleting it takes the arm to ~6s and makes it provider-free — but the
arm still loses to `vector@32`, so the cost is recoverable while the value is not.

## 1. Where hybrid-local's 226s goes *(provider)*

Instrumented `retrieve_for_arm(..., "hybrid-local")` on apd, 3 queries in one process so
model loads amortise. q2 is steady state.

| phase | q0 (cold) | q2 | q2 share |
|---|---|---|---|
| **total** | **237.19s** | **146.55s** | 100% |
| graphrag synthesis stream | 211.76s (ttft 176.0) | 114.64s (ttft 78.8) | **78.2%** |
| cross-encoder rerank (vector half) | 10.65s | 20.49s | 14.0% |
| bge-m3 encode × 2 | 10.40s | 6.70s | 4.6% |
| residual (asyncio, litellm) | 3.7s | 3.8s | 2.6% |
| store queries | 0.33s | 0.36s | 0.25% |
| `_nodes_from_chunk_ids` | 0.153s | 0.148s | 0.10% |
| `_load_artifacts` | 0.159s | 0.083s | 0.06% |
| LanceDB open | 0.083s | 0.028s | 0.02% |

**The artifact-caching hypothesis is dead.** `_artifacts` is cached per instance
(`groundly/retrieval/graph.py:174-178`) but `agents/ask.py:89` constructs a fresh
`GraphLocalRetriever` per call, so the cache never survives a query — it does re-read
every time, and it costs **75–159 ms**. The five files total **4.81 MB on disk / 10.8 MB
in pandas**, not 50 MB; the 49 MB in `graph/` is `lancedb/` + `cache/` + `input/`, none of
which `_load_artifacts` touches. Across all 48 questions: **4–7s of a 10,473s sweep
(0.05%)**.

### The finding: the expensive call's output is thrown away

`groundly/retrieval/graph.py:189` — `_response, context_data = asyncio.run(local_search(...))`.
`_response` is discarded; only `context_data["sources"]` is used. graphrag builds the
context **before** the model call
(`graphrag/query/structured_search/local_search/search.py:154` → `on_context` 171 →
`completion_async` 173), so the returned chunk ids **cannot** depend on the synthesis.

Confirmed on `test_graph` in one process *(provider)*:

```
A) local_search (with synthesis): 60.67s -> 10 chunk ids
B) build_context only:             0.09s -> 10 chunk ids
identical order+content: True        SPEEDUP: 674x
```

On apd, provider-free, warm: `build_context` **0.14–0.15s** → 16/21/25 chunks. The prompt
the synthesis was prefilling is 6.9k–10.9k tokens, which is what the 79–176s TTFT buys.

Worse in the product path: `ask()` discards graphrag's answer and then makes its *own*
`complete("chat", ...)` call (`agents/ask.py:177-178`) — two syntheses per multi-hop
question, one never read.

## 2. Is the latency reducible enough to matter?

Reducible: ~97%, not by making graphrag faster but by not calling the part that generates
prose. Projected steady state: **226s → ~6s (37×), and the arm stops needing a provider**,
which also aligns it with the zero-key rule in `.claude/rules/architecture.md` and closes
the untraced-LLM-call gap flagged at `graph.py:23-26`.

Does it earn its place at 6s? **No.** Re-scored against the gold set, hybrid-local hits
**1/48** questions `vector@32` misses and misses **4/48** it finds — net **−3**, at equal
recall (0.648 vs 0.644), against `vector@32`'s 5.6s and zero build cost.

**Latency-comparison caveat for the thesis:** the vector arm measured 5.6s standalone but
**30.5s** interleaved with graph arms in the same sweep — a 5.4× penalty purely from a
resident 12B model sharing the machine. Cross-arm latencies from a mixed sweep are not
comparable.

## 3. graph-global returns 95% of the corpus

**The join costs 82 ms** (0.06% of a 146s query) — not a performance problem. It is a
structural one: `graph.py:249` reads `context_data["reports"]`, which measured **555 rows**
= every level≤2 report, i.e. the context builder's *input*, not what the map-reduce found
useful. Replaying the join with all 555 reproduces exactly 1,138 chunks. Hence the eval's
min = median = max = 1138: **the arm is a constant function of the corpus.**

Capping by a query-independent key only shrinks the constant (top-10 → 105 chunks, top-20
→ 342, top-50 → 651, top-200 → 1,078). The only query-dependent signal reachable without
redesign is the map phase's per-key-point scores via `QueryCallbacks.on_map_response_end`,
at **batch** granularity — 27 batches over 555 reports.

**The map phase almost certainly isn't answering.** `build_context` produces **27 batches**,
1,603,237 chars ≈ **318k prompt tokens per query** — 1.45× the whole 219,476-token corpus,
*per question*. At ~150 tok/s prefill that is ~35 min serialised; the eval observed 146s
with min 129s / max 147s. A 12% spread across 48 heterogeneous questions is a fixed-cost
failure path, not generation. Root cause candidate: `graph.py:96` never sets
`concurrent_requests`, leaving graphrag's default **25**, while the build path sets **1**
for loopback providers (`ingestion/graph.py:423`). 25 concurrent ~12k-token calls against
one shared KV cache is the exact failure `graphrag_adapter.py:258-288` documents, and
`_map_response_single_batch` swallows it into `{"answer": "", "score": 0}`.
**The build-path fix never crossed to the query path** — the fourth instance of that pattern.

**Product path is broken independently of latency:** `agents/prompts.py:54-64` has no cap,
so `ask` with router label `global` would push 1,138 chunks ≈ **183,220 tokens into a
16,384-token window — 11.2× overflow**. The eval never hit this because it is
retrieval-only.

## 4. The 15h build

From `~/.groundly/apd/graph/stats.json`:

| workflow | seconds | share |
|---|---|---|
| `create_community_reports` | 32,092.3 | 59.4% |
| `extract_graph` | 21,551.5 | 39.9% |
| `generate_text_embeddings` | 416.8 | 0.8% |
| others | 3.9 | 0.007% |

Token accounting recovered from graphrag's own cache, reports mapped to level (0 unmatched):
level 0 = 38 reports / 199k prompt tok; level 1 = 203 / 585k; level 2 = 314 / 774k;
**level 3 = 54 / 137k (8.1%)**.

**Capping at level 2 saves 0.71h and costs nothing** *(estimated)* — all **309** entities in
level-3 communities also appear at level≤2 (**0 exclusive**), and `COMMUNITY_LEVEL = 2`
means level 3 is never read. But `ClusterGraphConfig` has no `max_level`, so this is a
pipeline interception, not a config change. 0.71h does not justify that.

**The knob that exists and is unset is `max_cluster_size`.** `ingestion/graph.py:419-455`
never sets `cluster_graph`, so it runs graphrag's default of 10. Re-clustering apd
(0.05s, no LLM):

| max_cluster_size | communities |
|---|---|
| **10 (current)** | **609** |
| 20 | 406 |
| 30 | 309 |
| 50 | 203 |

At `max_cluster_size=50`: **8.91h → 3.11h, total build 15.0h → 9.2h (−39%)** *(estimated)*.
It also cuts level≤2 reports 555 → 203, dropping global search's map phase from 27 batches
to ~10. Level 0 still covers all 2,389 clustered entities; what is lost is summary
granularity, not reach.

## 5. The un-truncated RRF

`agents/ask.py:99` — no `[: context_k]`. Median 33 chunks against `context_k = 8`
(min 19, max 55). Re-scored offline, truncating the arm's own fused order:

| k | hit | recall | MRR |
|---|---|---|---|
| **8** | **0.667** | **0.451** | **0.265** |
| 20 | 0.812 | 0.613 | 0.277 |
| **full (33)** | **0.833** | **0.648** | **0.277** |
| *vector@8, same run* | *0.708* | *0.493* | *0.340* |

**Dilution is not the cause.** Truncating to the configured 8 moves MRR 0.277 → 0.265
(*worse*) and costs 0.167 hit / 0.197 recall — and at its own configured k the arm loses to
the vector baseline on every metric. The fused order is bad at the *top*: `rrf`
(`retrieval/vector.py:27-34`) gives equal-rank entries equal scores and `sorted` is stable,
so `graph[i]` always precedes `vector[i]` — a weak graph ordering owns rank 1. Hybrid puts
the first relevant chunk at rank 1 on **4/48** questions; vector@8 on **7/48**.

Performance angle is negligible (`_nodes_from_chunk_ids` is 148 ms at 29 nodes, 42 ms at
1,138). The real cost is downstream in `assemble()`: ~4× the generation prompt.

**Fix the truncation because the contract says 8 — but do not expect a quality win, and do
not ship it alone, because it makes the published numbers worse.**

## Findings

| # | file:line | cost | fix |
|---|---|---|---|
| 1 | `retrieval/graph.py:189` | 114.6–211.8s/query, 78–89% of hybrid-local | call `context_builder.build_context()`; drop the discarded synthesis |
| 2 | `retrieval/graph.py:249` | 1,138 chunks, identical for all 48 questions | `reports` is the map *input*; rank via `on_map_response_end` or stop treating the arm as ranked |
| 3 | `retrieval/graph.py:96` | 25 concurrent ~12k-token map calls at a loopback provider | pass `concurrent_requests()` as `ingestion/graph.py:423` does |
| 4 | `agents/prompts.py:54` | 183,220 tok into a 16,384 window (11.2×) | cap `assemble()` at `context_k` |
| 5 | `agents/ask.py:99` | median 33 vs `context_k=8` | `[: context_k]`, plus fix the tie-break ordering |
| 6 | `ingestion/graph.py:419` | 609 report calls → 8.91h | set `max_cluster_size=50`: est. −5.8h |
| 7 | `agents/ask.py:87-91` | query embedded twice, 2 × ~3.3s | share one encode |
| 8 | `retrieval/graph.py:87` | artifacts + engine rebuilt per query, 419 ms | hoist only *after* finding #1 |
| 9 | `retrieval/graph.py:263-307` | 82 ms | leave it — not the cost |

## The negative result, stated plainly

On apd (187 materials / 1,193 chunks) GraphRAG cannot be made competitive. A **15.02h**
build (8.91h of it community reports) buys an arm that, after removing 97% of its query
cost, hits **1** question `vector@32` misses and misses **4** it finds, at equal recall
(0.648 vs 0.644) — while `vector@32` costs 5.6s/query and zero build. `graph-global` is
worse than uncompetitive: it returns the identical 1,138-chunk constant for every question,
and its 27-batch / 318k-token-per-query map phase costs 1.45× the corpus per question.

The recoverable engineering waste is real and large (finding #1 alone is 37×), but
recovering it changes the graph arms from expensive and unhelpful to **cheap and
unhelpful**. That is the result worth writing up.

## Verification (by the parent session, independently)

- `graph.py:189` discards `_response` — confirmed by reading.
- graphrag builds context at `search.py:154` and calls the model at `search.py:173` —
  confirmed, so chunk ids provably cannot depend on the synthesis.
- The truncation table was re-derived from `results-20260807T205526` against gold labels
  resolved from the live store: k=8 → hit 0.667 / recall 0.451 / MRR 0.265; full → 0.833 /
  0.648 / 0.277. Matches exactly.

Agent probe scripts (read-only, in-process monkeypatching, no tracked file modified) were
written to the session scratchpad and are not preserved.
