"""Offline retrieval evaluation — the P5 gate ("graph arms measured on the gold set",
docs/groundly-spec.md §8) and the thesis's evidence base.

Client layer: this package imports `agents`/`retrieval`/`core`, and **nothing imports
it** (.claude/rules/architecture.md). It is a research surface, not a product surface —
no MCP tool and no runtime path depends on it.

**Provider requirements differ per arm, and the difference is not cosmetic:**

- `vector` is genuinely zero-key — no provider, no model call, offline.
- `hybrid-local` needs the `extraction` provider: graphrag makes ~1 synthesis call per
  question inside `local_search`.
- `graph-global` needs it far more heavily. `global_search` is **map-reduce over the
  community summaries**, batched to `GlobalSearchConfig.max_context_tokens` (12,000 by
  default, unoverridden), so the call count scales with total report volume — measured
  on apd, 555 reports at `level <= 2` totalling ~389k tokens give **~33 map calls plus
  one reduce, per question**. A 48-question sweep is ~1,600 provider calls, not 48.

All of it is invisible to the traces table (groundly/retrieval/graph.py's known gap: the
call is graphrag's own LiteLLM client, which never reports usage back through `llm/`), so
a graph-arm eval spends tokens this project cannot account for. That is a caveat the
thesis must state, not one the harness can measure away — and it is why the per-arm cost
warning distinguishes the two arms instead of averaging them.

Generation metrics (slice 2) need a chat provider on every arm.
"""
