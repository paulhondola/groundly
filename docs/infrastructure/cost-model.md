# Cost Model

There is no infrastructure cost: every cost is the **student's own provider key**, and only one operation spends it.

| Operation | Cost shape | Mitigation |
|---|---|---|
| Indexing (chunks + vectors) | **$0** — bge-m3 runs locally; time, not money | one-time per subject |
| `search`, `get_page` | **$0** — retrieval only | the whole query path |
| Verification (`submit_cards`) | **$0** — local re-retrieval | the host's own tokens write the cards |
| Anki export, import/export | **$0** | — |
| **Graph build** (`index --graph`) | **the only bill**: single-digit dollars per subject, corpus-size dependent | estimated and shown **before** the run; skippable; **sharing amortizes it** — one student builds, the course imports |

## Principles

1. **Zero-key operation is first-class.** A student with no API key loses only the knowledge graph.
2. **Every metered call passes through `llm/`** and records tokens + cost into the traces table. Visibility, not enforcement.
3. **Show cost before spending it**: the build prints a range with every assumption named, then asks.
4. **Sharing is the cost model**: the two expensive artifacts (graph, verified decks) are exactly the exportable ones. One payment, N beneficiaries.
5. **Reasoning tokens are billable and usually wasted here** (decision 24): output is ~94% of the graph bill, and a reasoning model spends most of it deliberating over a prompt that asks for a delimited tuple list. Set `providers.extraction.reasoning_effort` whenever the model reasons by default.

## Local-runtime note

Pointing `extraction` at LM Studio/Ollama makes the build token-free, but keeps a **model-class floor**: roughly 12B with reasoning verified off. Below it the graph is not merely slower but wrong — `qwen3.5:4b` produced 1–44 malformed records per call against a 12B's zero, and graphrag drops malformed records silently. A local build also serializes (decision 25), so it trades money for hours.
