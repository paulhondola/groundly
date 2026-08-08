# Retire the graph retrieval arms from the product path — design (2026-08-08)

## Context

P5 built two graph retrieval arms (`hybrid-local`, `graph-global`) and a router that
selects between them and the vector baseline. The eval harness measured them fairly for
the first time on 2026-08-08, after correcting three defects in the harness itself
(set-size-matched scoring, leakage base rate, paired significance — decision 27).

The measurements say the arms lose, and a follow-up diagnosis says *why*. This change
removes them from the product path while keeping them reproducible from shipped code, and
removes the router from `ask()` because it no longer has anything to select.

**It retires this implementation, not the idea.** That distinction is load-bearing and is
recorded below, because a repaired arm measures differently from the shipped one.

## The measurements this rests on

All on apd (187 materials / 1,193 chunks / 48 gold questions), set-size-matched. The
`vector` and `hybrid-local` figures come from one provider-free sweep (local search stopped
at `on_context`, decision 27). `graph-global`'s are structural properties of its citation
join, derivable from the parquet artifacts without a provider.

**1. The shipped arms lose.**

| arm @ k=8 | hit | recall | MRR |
|---|---|---|---|
| `vector` | **70.8%** | **0.493** | **0.340** |
| `hybrid-local` (shipped) | 60.4% | 0.375 | 0.262 |
| graph half alone (shipped) | 43.8% | 0.234 | 0.190 |

`vector` wins at every cutoff (k = 1, 5, 8, 10, 20) on every metric. The unmatched table
reads the other way (`hybrid-local` 92% hit vs 90%) purely because it returns 42 chunks to
vector's 20 — the set-size artifact decision 27 exists to expose.

`graph-global` is worse than uncompetitive: it returns the **same 1,138 chunks for every
question** (95% of the corpus) in ascending rowid order, so it has no rank at all and is
excluded from matched-cutoff scoring and significance.

**2. The cause is the arm's construction, not the graph.**

- 1,175 / 1,193 chunks are reachable via some entity; 79 / 80 gold chunks are reachable.
- Of the graph arm's misses, **0 were structurally impossible** and 17 had reachable gold.

So the graph is sound and the failure is anchoring plus ordering: `top_k_entities = 10`
against 2,685 entities (0.37%), `text_unit_prop = 0.5`, and **no relevance model at all** —
`_nodes_from_chunk_ids` scores `1.0 / (rank + 1)` over graphrag's context-assembly order,
i.e. position in a prompt. The vector arm meanwhile runs dense + sparse + BM25 over the
whole corpus and reranks with a cross-encoder. The comparison was never like-for-like.

**3. A repaired arm is neutral, not harmful.** Adding the same cross-encoder and widening
anchoring (`top_k_entities=30`, `text_unit_prop=0.8`) lifts the graph arm to 56.2% hit@8,
and fusing *that* at reduced weight improves rank metrics over vector alone:

| k | `vector` alone | RRF(vector 1.0, repaired graph 0.25) |
|---|---|---|
| 1 | 14.6% / MRR .146 | 18.8% / MRR .188 |
| 8 | 70.8% / MRR .340 | 70.8% / MRR .354 |
| 10 | 70.8% / MRR .340 | 75.0% / MRR .358 |
| 20 | 89.6% / MRR .354 | 89.6% / MRR .369 |

Every one of those gains is **2 questions**. This gold set needs roughly a 9-question split
before exact McNemar clears p < 0.05, and a net difference of 2 cannot reach significance
under any discordant split. So this is **absence of harm, not evidence of benefit** — and
it is the reason the retirement is scoped to the implementation.

**Provisional, explicitly:** the repaired configuration exists only in a session probe, not
in shipped code, so it is not reproducible from the repository. It must be marked
provisional wherever it is cited until it is either landed or re-measured from shipped code.

## Decisions

| decision | choice |
|---|---|
| Scope of "retire" | Drop from the product path; **keep selectable in `groundly eval`** |
| Router on the ask path | **Remove from `ask()` entirely** |

Keeping the arms in the eval preserves the negative result's reproducibility from shipped
code — the thesis chapter depends on it, and `eval/` is a research client that may depend on
services (`.claude/rules/architecture.md`; the layering rule only forbids the reverse).

## What changes

### `groundly/agents/ask.py` — the core change

- **Delete `_LABEL_TO_ARM`.** It is the only thing that turns a router label into a graph arm.
- **Delete the `classify()` call.** `router_label` stays in the trace schema and is always
  `None` from `ask`, so the column keeps meaning "what the router said" rather than being
  repurposed.
- **Delete the `arm=` parameter.** While it exists the product can still reach a retired arm.
  Nothing in production passes it — only tests — because the eval calls `retrieve_for_arm`
  directly. Removing it is what makes the retirement real rather than nominal.
- `ask()` calls `retrieve_for_arm(subject, query, "vector", ...)` unconditionally.
- **Add `PRODUCT_ARMS = ("vector",)`** beside the existing `ARMS`, so "which arms may the
  product select" is an explicit, testable fact rather than an absence.

Net effect: one fewer provider call per `ask`, and no code path from a user question to a
graph arm.

### Everything else

| file | change |
|---|---|
| `groundly/cli/ask.py:62` | drop the `router=` field; it would print `—` forever |
| `groundly/agents/router.py` | docstring: no runtime caller; retained for eval measurement |
| `tests/agents/test_agents_ask.py` | replace the ~6 routing tests with tests asserting the product **cannot** select a graph arm |
| `docs/architecture/retrieval.md` | rewrite the "Forcing an arm" paragraph (describes `ask(arm=)`) |
| `docs/architecture/agents.md:68` | pipeline line still leads with `router → retrieval arm(s)` |
| `docs/groundly-spec.md` | new decision **28** recording the retirement and its measurements |

### Deliberately unchanged

- **`retrieve_for_arm` keeps all three arms** — it is the eval's entry point.
- **`groundly eval --arms vector,hybrid-local,graph-global` keeps working.**
- **`drill_down()` / `overview()` (UC-12) are untouched.** They reach the same retrievers via
  `agents/study_modes.py`; that structural use is the graph's remaining justification.
- **`groundly/retrieval/graph.py` is untouched.** Both retrievers stay.
- **`UNRANKED_ARMS`** stays — the eval runner uses it.
- **`llm/rerank.py`'s `use_fp16=False`** stays. Measured this session: fp16 costs **3.98 GB**
  against fp32's **0.85 GB** on Apple Silicon, because FlagEmbedding's fp16 path moves the
  model to MPS and materialises an anonymous copy while fp32 stays memory-mapped. Decision 19's
  fp16 win for bge-m3 came from a different mechanism and does not transfer.

## What a future attempt would need

Recorded so the retirement is not read as "graph fusion cannot work":

1. **A relevance model on the graph arm** — the single largest lever (+10.4 points hit@8).
2. **Reduced fusion weight** — equal-weight RRF assumes comparable arms; at w=1.0 the repaired
   arm still hurts (66.7% hit@8), at w=0.25–0.5 it does not.
3. **Wider anchoring, but only alongside reranking** — `top_k_entities` 10→30 changes nothing on
   its own, because graphrag's context builder appends rather than reorders.
4. **A larger gold set.** At n = 48 nothing below ~9 questions is resolvable, and the effects in
   play are ~2.

## Verification

1. Full suite green (594 at time of writing).
2. New test: `ask()` exposes no `arm` parameter and no code path reaches
   `GraphLocalRetriever` / `GraphGlobalRetriever`.
3. New test: `PRODUCT_ARMS` is a strict subset of `ARMS`.
4. Research surface intact — this must still run and reproduce today's numbers:

```bash
.venv/bin/groundly eval apd --arms vector,hybrid-local --at-k 1,5,8,10,20
```

5. UC-12 unaffected — `drill_down` / `overview` still resolve citations to verbatim chunks.
6. Review with `spec-guardian` before commit; branch `p5-eval-harness`, never `main`.

## Out of scope

- Removing `graph-global`'s citation join or narrowing it (decision 27's open risk).
- Changing `COMMUNITY_LEVEL` from 2 to 0 (measured 11× cheaper for `overview()`, but that is a
  UC-12 change, not a retirement).
- The structural use cases the graph is being kept for — coverage-balanced generation
  (UC-10/UC-11) and study-gap analysis (UC-14). Each needs its own spec.
- The broader simplification pass (removing unused or non-working subsystems), which is
  deliberately deferred to a separate session.
