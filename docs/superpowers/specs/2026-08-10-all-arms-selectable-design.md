# All arms selectable: removing the product/dev split

**Status:** design, approved 2026-08-10 (Paul). Branch `all-arms`, off `main` at 6a44955.

## Context

Decision 28 (2026-08-08) split the arms in two: `ARMS` is what `groundly eval` may
score, `PRODUCT_ARMS = ("vector",)` is what a user question may reach. The rationale was
that measured on apd the vector arm won at every cutoff the product uses, so the product
should ship the winner while the research surface kept every arm runnable.

That split is now getting in the way of the thing it was meant to serve. Generation-side
numbers — citation accuracy, faithfulness, cost per answer — come from full `ask()` runs
reading the traces table (decision 27's "slice 2"), and there is currently **no way to run
`ask()` through anything but the vector arm**. The comparison the thesis rests on cannot be
extended past retrieval without either re-adding arm selection or duplicating the ask
pipeline inside the eval.

So: remove the product/dev distinction entirely. Every implemented arm is selectable
everywhere it makes sense, for full testing.

**This reverses decision 28's headline claim.** Decision 28 must be rewritten, not
amended — "the product ships one retrieval arm" stops being true. What survives is the
*measurement* it rests on (vector wins at every cutoff the product uses) and the default
(`ask` still uses vector unless told otherwise).

## Decisions

Answered by Paul, 2026-08-10:

1. **Explicit selection only; default `vector`.** No router on the ask path. Decision 28
   removed `classify()` because a classifier among one arm is a wasted round-trip; it
   stays removed on its own merits — measured 47.9% against 45.8% for a constant
   classifier, and it sent 30 of 48 questions to the most expensive arm.
2. **CLI only. The MCP `ask` tool keeps its current signature and stays on `vector`.**
   Arm selection is a testing and thesis affordance, so it lives on the surface a human
   drives. A host model picking `graph-global` on a whim is ~33 untraced provider calls
   per query that the student pays for. Graph access from MCP remains `drill_down` and
   `overview`, which exist for exactly that (UC-12).
3. **Arm 4 (`adaptive`) stays declared-but-not-implemented** (`build=None`). Building the
   adaptive loop is a feature with its own design, cost profile and eval story.
4. **A graph arm on a subject with no graph fails loudly.** No silent degradation to
   vector.

Derived from (4), and the largest structural consequence:

5. **Degradation is removed as a concept.** It exists today only inside
   `retrieve_for_arm`, and its only two callers now both want it gone: the eval already
   treats it as fatal (`ArmDegradedError`), and a user who typed `--arm hybrid-local`
   does not want vector's numbers under that name. Removing it makes
   `retrieve_for_arm`'s third return value (`arm_actual`) always equal `arm`, so the
   return collapses to a 2-tuple and `ArmDegradedError` becomes unreachable.

## What changes

### 1. `groundly/retrieval/arms.py`

- Delete the `product` field from `Arm` and the `PRODUCT_ARMS` derived view.
- Keep `needs_graph` — it stops being a fallback trigger and becomes the *preflight*
  predicate (below).
- `retrieve_for_arm` returns `(nodes, path)`. `GraphNotBuiltError` propagates instead of
  being caught; the `logger.info("degrading to vector-only")` branch and `_build_vector`
  fallback go with it.
- Add `requires_graph(names)` → bool, so callers can preflight a batch without
  constructing retrievers.

### 2. New shared graph-built check

`groundly/core/subject.py` gains `Subject.graph_is_built() -> bool`:

```python
def graph_is_built(self) -> bool:
    """A *recorded* graph, not merely a directory. A refused or interrupted build
    deliberately leaves partial parquet behind so the retry keeps graphrag's paid-for
    cache; only `corpus_hash` is written by a build that passed every gate."""
    return (self.root_dir / "graph").exists() and self.load_manifest().graphrag.corpus_hash is not None
```

This predicate is currently written out **seven times** across five files in three
slightly different shapes. Replace the four that ask exactly this question:
`retrieval/graph.py:214` (`_require_graph`), `mcp/server.py:132` (`list_subjects`'
`graph_built`), `cli/subjects.py:168`, `cli/subjects.py:339`.

**Deliberately not replaced:** `ingestion/graph.py:445` and `:546` ask a narrower
question (is a hash *recorded*, ignoring the directory) inside the build's own gates, and
`core/graph_html.py:455` additionally requires `entities.parquet`. Folding those in would
change behaviour, which this change set is not for.

### 3. `groundly/agents/ask.py`

```python
def ask(subject, query, *, arm: str = VECTOR, rerank=True, embedder=None, reranker=None) -> AskResult:
```

- Validate `arm` via the existing `validate_arms([arm])`, so an unknown arm and an
  unimplemented one get their two distinct messages (the shared screen added in #31).
- Preflight: if `ARM_TABLE[arm].needs_graph` and not `subj.graph_is_built()`, raise
  `GraphNotBuiltError` **before** `require_provider("chat")` and before any model load —
  nothing started, nothing to trace, and no provider call paid for a run that cannot work.
- `trace.arm = arm` directly; there is no longer an `arm_actual` to report.
- The module docstring's "**The product path is the vector arm, and only the vector
  arm**" paragraph is rewritten.

### 4. `groundly/cli/ask.py`

```
groundly ask SUBJECT "question" [--arm vector|hybrid-local|graph-global]
```

- `--arm`, singular, beside the existing `--arms` (plural, comma-separated) on
  `groundly eval` — different surfaces, unambiguous names.
- Help text names the cost asymmetry: `vector` and `hybrid-local` are provider-free on
  the retrieval half; `graph-global` runs graphrag's map-reduce, tens of untraced calls
  per query.
- `GraphNotBuiltError` joins the existing `except` tuple so a missing graph prints a named
  cause, not a traceback.

### 5. `groundly/eval/runner.py`

- Preflight once, before the first question: if any requested arm needs a graph and the
  subject has none, raise immediately. This is strictly better than today's
  detect-degradation-on-question-1 — it is the same shape as the existing up-front
  `validate_arms` call, and it cannot be absorbed by the per-question error handler.
- Delete `ArmDegradedError` and the `arm_actual != arm` check; update `cli/eval.py`'s
  `except` clause. `GraphNotBuiltError` becomes the fatal error the CLI reports.

### 6. Optional, low priority — `groundly search --arm`

Retrieval-only arm selection without generation cost. **Recommend cutting it:**
`groundly eval` already is the retrieval-only measurement tool, so the marginal value is
small, and `search` is the zero-key path — `graph-global` there would need a provider,
which muddies a guarantee that is currently absolute. Listed so the decision is explicit
rather than an oversight.

## Tests

Three existing tests exist specifically to assert the thing being removed. They are
**rewritten to assert the new contract**, not deleted, so the change is visible in the
suite rather than silent:

| Test | Becomes |
|---|---|
| `test_ask_cannot_reach_a_graph_arm` | `test_ask_reaches_the_arm_it_is_given` — each arm, asserting the trace records it |
| `test_ask_takes_no_arm_parameter` | `test_ask_defaults_to_vector` — signature default is `VECTOR`, and an `ask()` with no `arm=` traces `vector` |
| `test_product_arms_is_a_strict_subset_of_arms` (two files) | deleted; `PRODUCT_ARMS` no longer exists |

New:

- `test_a_graph_arm_fails_loudly_without_a_graph` — `ask(..., arm="hybrid-local")` on a
  graph-less subject raises `GraphNotBuiltError`, writes **no** trace row, and makes no
  provider call. The last two are the point: the preflight must land before both.
- `test_eval_preflights_the_graph_requirement` — `run()` raises before question 1, not
  during it.
- `test_graph_is_built_agrees_with_every_call_site` — the new `Subject.graph_is_built()`
  returns what the replaced inline checks returned, including the partial-build case
  (directory present, `corpus_hash` None).
- `retrieve_for_arm` returns a 2-tuple; `GraphNotBuiltError` propagates for
  `needs_graph` arms.

## Docs (same change set — docs are the source of truth)

- **`docs/groundly-spec.md` decision 28** — rewritten. New framing: the product *defaults*
  to the measured winner and every arm stays selectable, because the comparison is the
  contribution and extending it to generation metrics requires the ask pipeline. The
  measurements decision 28 records are unchanged and stay.
- **`docs/architecture/retrieval.md`** — arm table Status column (line 9), the
  `ARM_TABLE`/derived-views paragraph (14), the `PRODUCT_ARMS` sentence (16), the router
  section's "once `PRODUCT_ARMS` holds one arm" argument (96), and "There is deliberately
  no `ask(arm=...)`" (118), which becomes its opposite with the reasons restated.
- **`docs/architecture/agents.md:70`** — "No router, and one arm".
- A new decision entry recording *this* reversal, via `/decision`, rather than editing 28
  to pretend it always said this.

## Verification

- Full suite green; new count stated explicitly (baseline 608).
- `groundly ask apd "..." --arm vector` and `--arm hybrid-local` both produce cited
  answers on a subject with a graph; the traces table records the arm actually used.
- `groundly ask <graphless-subject> "..." --arm hybrid-local` fails with a named cause and
  writes no trace row.
- `groundly eval` unchanged in output on a graph-backed subject — this change must not
  move any published number. Same evidence standard as #31: compare per-question
  retrieved chunk ids against a pre-change run on the same graph, not just the summary
  metrics.
- `spec-guardian` and `security-reviewer` before the PR.

## Out of scope

- Implementing arm 4.
- Re-admitting the router to the ask path.
- Arm selection on the MCP surface.
- Folding `drill_down`/`overview` into arm selection — they are UC-12 study modes with
  their own signatures (entity, topic) and `overview` returns communities. They are not
  arm selection and unifying them would be a product change, not a cleanup.
