# Cost Model

There is no infrastructure cost and **Groundly spends no money**: it holds no API key and makes no metered call (decision 37). The model that composes answers and writes quizzes is the student's own AI subscription, billed by that host, not by Groundly.

| Operation | Cost shape | Mitigation |
|---|---|---|
| Indexing (chunks + vectors) | **$0** — bge-m3 runs locally; time, not money | one-time per subject |
| `search`, `get_page` | **$0** — retrieval only | the whole query path |
| Verification (quizzes, planned) | **$0** — local re-retrieval | the host's own tokens write the questions |
| Import/export | **$0** | — |
| Page chores (Create topic map, Write questions, Grade now) | Each click starts one run on the student's existing Claude Code plan (decisions 46 and 55) | The switcher selects the model and effort. Runs use Groundly's tools only. A prior Claude Code measurement was $0.077 against $0.27 for the same task with the full setup. |

## Principles

1. **Zero-key is total.** No code path constructs an LLM client and no config asks for a key.
2. **Sharing still pays**: the index is the expensive artifact (extraction, OCR and embedding time) and it is exportable. One student indexes, the course imports.
