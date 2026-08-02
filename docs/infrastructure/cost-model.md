# Cost Model

The archived iteration budgeted a VM and platform-paid tokens. Post-pivot there is no infrastructure cost at all — every cost is the **student's own provider key**, and the design's job is to make each spend visible before it happens and amortizable across the course.

## Per-operation costs (student-side)

| Operation | Call class | Cost shape | Mitigation |
|---|---|---|---|
| Indexing (vectors) | — (local models) | **$0** — bge-m3 runs locally; time, not money | one-time per subject |
| `search` (MCP) | — | **$0** — retrieval only | the free path hosts prefer |
| `ask` | router + chat | ~fractions of a cent per question | router avoids graph paths for factoids |
| Graph build | extraction | **the big one**: single-digit dollars per subject (corpus-size dependent) | estimated and shown **before** the run; skippable; **sharing amortizes it** — one student builds, the course imports |
| Graph global search | chat (map-reduce) | 10–50× a factoid answer | fires only via router classification or explicit `overview` |
| Thick generation (`generate_deck`/`generate_quiz`) | generation | 2–4× base generation (verifier loop) | one-time per deck; verified decks are exportable — amortized like the graph |
| Thin generation (`submit_*`) | — | **$0 to Groundly** — the host agent's subscription generates; Groundly only verifies | the zero-key path |

## Principles

1. **Zero-key operation is first-class**: index, search, thin generation, Anki export, mastery, memory — all free. A student with no API key loses only `ask`, thick generation, and graph arms.
2. **Every metered call passes through `llm/`** and records tokens + cost into the traces table — per call class, per day, queryable. Visibility, not enforcement: it's the student's key and the student's call.
3. **Show cost before spending it**: any operation expected to exceed ~cents (graph build, bulk generation) prints an estimate and asks.
4. **Sharing is the cost model**: the two expensive artifacts (graph, verified decks) are precisely the exportable ones. Course-level economics: one payment, N beneficiaries.
5. **Reasoning tokens are billable and usually wasted here** (decision 24): output is ~94% of the graph bill, and a reasoning model spends most of it deliberating over a prompt that asks for a delimited tuple list. Measured completion:prompt was 4.06:1 on a reasoning model against 0.87:1 on four others; locally the same tax showed up as 9.2× wall clock. `providers.extraction.reasoning_effort` is therefore a first-order cost lever on cloud, not just a local speed knob — set it whenever the extraction model reasons by default.

## Local-runtime note

Pointing call classes at LM Studio/Ollama makes everything token-free but: evaluation runs must use the recorded provider config (results from ad-hoc local models are invalid for the thesis), and the extraction class keeps a **model-class floor** — a bad graph silently invalidates the comparison. Decision 24 replaced the flat "mid-tier cloud model" rule with the measured version: roughly 12B, reasoning *verified* off, at `graph.context_window` 12288. Below that floor the graph is not merely slower but wrong — `qwen3.5:4b` produced 1–44 malformed records per call against a 12B's zero, and nothing surfaces that, since graphrag drops malformed records silently and the 5% gate counts call failures rather than dropped records. Cloud remains the default recommendation; `graph.report_call_class` exists so the json_schema-dependent community-report stage can stay on a cloud provider while the ~1,194-call extraction pass runs locally.
