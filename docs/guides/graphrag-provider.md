# Configuring the GraphRAG provider

`groundly index --graph` builds the graph retrieval arm: entity/relation
extraction, Leiden community detection, and hierarchical summarization over
your indexed materials. Extraction is a real LLM call per chunk — this is the
one call class that needs a specific kind of provider, not just any
configured endpoint.

## Why extraction shouldn't be a small local model

LM Studio/Ollama work mechanically for `extraction` — it's the same
OpenAI-compatible `base_url`+`model`+`key` shape as every other call class,
and an unset key is fine (Groundly passes a placeholder graphrag's own
config validation requires, same as any local provider). But a weak
extraction model produces a bad graph — sparse or wrong entities, garbled
relationships — and a bad graph silently invalidates the whole point of
having one. The rule
is: extraction needs a mid-tier cloud model, never a small local model. If
you don't want to spend on this, skip `--graph` entirely — the vector arm
works with zero API key, and `groundly index` (without `--graph`) is
completely unaffected.

## Configure it

```sh
groundly config set extraction.base_url https://api.<provider>.com/v1
groundly config set extraction.model <mid-tier-model>
groundly config set extraction.key <your-api-key>
```

If `<mid-tier-model>` is in litellm's bundled price map (most mainstream cloud
models are), cost tracing works automatically — no extra config. For a
local/unmapped model, set the optional per-token override fields instead:
without them, `groundly index --graph` still prompts for confirmation before
building, but can't show you a dollar estimate first:

```sh
groundly config set extraction.input_price_per_mtok <price per 1M input tokens>
groundly config set extraction.output_price_per_mtok <price per 1M output tokens>
```

`groundly config` shows the effective value (key masked) for every call
class, including `extraction`.

## Build the graph

```sh
groundly index <SUBJECT> --graph
```

On first build, this prints a rough cost estimate — chunk text ÷ 4 **plus
graphrag's ~1620-token few-shot preamble for every chunk**, which is usually
the bulk of it (on a 1194-chunk corpus averaging 156 tokens per chunk, the
preamble is 91% of every call and 1.9M of the 2.1M total tokens). Priced
against `extraction.input_price_per_mtok` if set, else litellm's bundled
price map for `extraction.model`, matched either bare (`gpt-4o-mini`) or by
provider prefix (`groq/llama-3.3-70b-versatile`). It then asks for
confirmation —
`--yes`/`-y` skips the prompt. If the model is priced by neither, you'll see
"no cost estimate available" instead of a dollar figure, but you're still
asked to confirm before anything is sent anywhere.

Once a subject has a graph, `--graph` is no longer needed: every later
`groundly index` run checks whether the graph still describes this subject —
the corpus changed (a material was added, removed, or re-extracted), *or* the
extraction prompt or entity types changed — and rebuilds automatically if so.
Same confirmation gate, same `--yes` skip. The message names which of the two
it was.

## What the graph is built to look for

Groundly ships its own entity-extraction prompt, tuned for course material.
graphrag's default is aimed at news: its entity types are
`organization/person/geo/event` and its worked examples are a stock-market
report and a political summit. On a parallel-algorithms corpus that produced
75 `ORGANIZATION` and 34 `EVENT` entities out of 115 — a graph of the wrong
*kind*.

The bundled prompt looks for:

```
concept, algorithm, data_structure, theorem, technique, tool, metric, person
```

`person` stays because courses cite Dijkstra and Lamport, and those are real
nodes. These defaults lean CS-ward, matching the pilot subjects; a law or
history course wants different ones:

```bash
groundly config set graph.entity_types "case,statute,court,doctrine,person"
```

You can replace the whole prompt too — this is also how the thesis evaluation
compares prompts on the gold set:

```bash
groundly config set graph.extraction_prompt /path/to/my_prompt.txt
```

A custom prompt must keep `{entity_types}` and `{input_text}`, which graphrag
substitutes, and must **not** contain `{tuple_delimiter}`, `{record_delimiter}`
or `{completion_delimiter}` — despite appearing in older graphrag prompts,
those are not substituted, and leaving one in makes every chunk fail silently.
Both problems are refused by name before any call is made.

Changing either setting changes the graph's extraction fingerprint, so the next
`groundly index` offers a rebuild rather than quietly serving a graph built
under different framing.

## Context size — the thing that actually bites

Entity extraction is the largest prompt in the build: the whole few-shot
preamble plus one chunk, every time. Because Groundly's chunks average ~156
tokens, that preamble is the *majority* of every call — which is why the
bundled prompt is ~700 tokens where graphrag's is ~1620, and why a 1194-chunk
build costs ~1.0M tokens instead of ~2.1M.

graphrag's own stage defaults still assume a large cloud model —
`community_reports` alone asks for 8000 tokens in and 2000 out — so stock
graphrag wants roughly **16k of usable context**.

A local model loaded at 4096 (LM Studio's common default) fails every single
call with `Context size has been exceeded`, and graphrag *swallows* those
failures per chunk, so without the guards below you'd get hours of work and
an empty graph.

So Groundly scales every budget to one number:

```bash
groundly config set graph.context_window 8192
```

Set it to whatever your model is actually loaded with — in LM Studio that's
the context length in the model's load settings, not the model's advertised
maximum. The default is 4096, which works out of the box but produces smaller
community summaries (weaker `overview`/global search). At 16384 and above,
Groundly reproduces stock graphrag exactly, including the gleaning pass.

Raising the model's context is the better fix when your hardware allows it:
more context means richer community reports, which is most of what the global
arm answers from.

## The extraction model must support JSON-schema structured output

Community reports — what global search and `overview` answer from — are the one
stage graphrag requests structured output for. It passes a Pydantic model, which
litellm turns into `response_format: {"type": "json_schema", …, "strict": true}`.

**This is stricter than "JSON mode".** The older `{"type": "json_object"}` is a
different capability, and endpoints disagree about which they accept — in both
directions. Measured 2026-07-26:

| endpoint | model | `json_object` | `json_schema` |
| --- | --- | --- | --- |
| api.deepseek.com | `deepseek-v4-flash` | yes | **no** |
| api.deepseek.com | `deepseek-v4-pro` | yes | **no** |
| api.deepseek.com | `deepseek-chat`, `deepseek-reasoner` | yes | **no** |
| LM Studio | `qwen/qwen3.5-9b` | **no** | yes |

So DeepSeek cannot build a graph today, whichever model you pick — it answers
`This response_format type is unavailable now`. LM Studio is the exact inverse
and refuses `json_object` with `'response_format.type' must be 'json_schema' or
'text'`.

Support is a property of the **model**, not the provider — on Groq,
`llama-3.3-70b-versatile` has no schema support while `openai/gpt-oss-120b` and
`llama-4-scout` do. Don't reason about it one provider at a time.

Nor can you trust a capability table, litellm's included: its bundled map marks
`deepseek-chat` as supporting response schemas, and the live API rejects it. The
preflight probe is the only reliable answer, which is why it sends graphrag's own
response model rather than an approximation of it — a model that can't do this
fails in seconds instead of after the whole extraction pass.

Left unchecked, the failure is confusing: every report call fails, graphrag is
left with an empty reports table, and it dies merging that table on a column an
empty frame doesn't have — `KeyError: 'community'`, tens of minutes into a run
whose extraction stage worked perfectly. That is exactly what happened on
2026-07-26 — 679s and 2.96M tokens of flawless extraction, then 436 failed
reports — because the probe was checking `json_object`, which DeepSeek accepts.

## Rate limits, if your provider has them

A graph build fires hundreds of concurrent calls, and graphrag swallows a 429
per chunk exactly like any other failure. Groundly always retries with
jittered exponential backoff, but it can only throttle if you tell it the
limits — there's no portable way to discover them, and guessing would slow a
local runtime that has none:

```bash
groundly config set extraction.tokens_per_minute 6000
```

Both `tokens_per_minute` and `requests_per_minute` are optional and
independent; set whichever your tier publishes. Unset means unthrottled,
which is the right default for LM Studio or Ollama.

Note this cannot help with a **per-day** cap. Groq's free tier allows 100k
tokens/day, and a 1200-chunk subject needs around 1.0M with the bundled prompt
— still ten days of quota. Throttling paces spending; it doesn't create
budget. Size the job first: the confirmation prompt's token figure is the
number to compare against your daily allowance.

## What else needs configuring

Extraction only covers the build. Two other call classes matter once the
graph exists:

- **`chat`** — every `ask` (multi-hop/global routed), `drill_down`, and
  `overview` call still needs a configured chat provider for Groundly's own
  answer synthesis, same as the vector arm. This can be local (LM Studio) —
  see [using-lm-studio.md](lm-studio.md).
- **`router`** — the query classifier that decides whether `ask` actually
  routes to a graph arm at all. Unconfigured or unreachable, every query
  degrades to `factoid` → vector-only, so a built graph never gets used by
  `ask` (though `drill_down`/`overview` still work directly, since they
  don't go through the router).

Mixing providers is normal — e.g. a cheap cloud model for `extraction`, a
local model for `chat`, nothing for `router` if you don't need `ask` to
auto-route.

## What leaves your machine

This is the one path in Groundly where your course materials' text gets
sent to a provider beyond what `ask` already sends per-question: extraction
reads every indexed chunk once, in full, to build the graph. It goes only to
whichever provider you configured for `extraction` — nothing else changes
about Groundly's privacy model (`progress.db` still never exports, `graph/`
travels with the rest of the subject on export like `store.db` does).

## Troubleshooting

- `[providers.extraction] is not configured` — the section above is missing
  from `~/.groundly/config.toml`, or a stale-graph auto-rebuild fired with no
  extraction provider set. Configure it (or delete `<subject>/graph/` if you
  no longer want a graph for this subject).
- `graph not built for this subject — run \`groundly index --graph\` first`
  — from `drill_down`, `overview`, or a multi-hop/global `ask` on a subject
  that's never had `--graph` run. Vector-only `ask` still works.
- Import dropped an imported bundle's graph and printed a note — the
  bundle's `graph/` didn't match its own `store.db` (tampered, stale export,
  or an embedding-pin change triggered a re-embed). Rebuild locally with
  `groundly index --graph` if you want one.
- `graph build failed: workflow(s) <names> failed` — graphrag ran but one or
  more of its workflows errored. The manifest is deliberately *not* stamped in
  this case, so the graph stays marked stale and the next `groundly index`
  retries it. Re-run with `--debug` for graphrag's own log lines naming the
  cause (commonly the extraction provider rate-limiting or returning
  unparseable output).
- `graphrag config is invalid` — the pinned graphrag version rejected the
  generated config. The details are withheld on purpose: they would include
  your `extraction` api_key.
- `the extraction model rejected a ~N-token probe prompt: …` — the preflight
  check. Before touching your corpus, Groundly sends one real extraction
  prompt; if it comes back `Context size has been exceeded`, the build stops
  in seconds instead of failing silently for hours. Raise your model's
  context, or lower `graph.context_window` (see above).
- `entity extraction failed for N of M chunks — … not recorded and stays
  unusable until a build succeeds` — graphrag catches extraction errors per
  chunk and carries on, so a build can "succeed" having indexed almost
  nothing. Above 5% failures Groundly refuses to record the build: the graph
  stays stale, the next `groundly index` retries it, and until then
  `drill_down`/`overview` report the graph as not built. The partial files
  are left on disk on purpose — graphrag caches the LLM responses it already
  paid for, so the retry is much cheaper. Below 5% the build completes and
  the count is printed alongside "Graph built".
- `graph build produced no entities` — the pipeline ran but extracted
  nothing at all. Re-run with `--debug` for graphrag's own errors.
- **A failed rebuild leaves no graph, not the old one.** Every build starts by
  clearing the previous artifacts, keeping only graphrag's LLM cache (so the
  retry is cheap) and the log. That's deliberate: a rebuild only runs once your
  corpus has changed, so the old graph was already not a graph of the current
  materials, and serving it would be a lie. Until a build succeeds,
  `drill_down`/`overview` report the graph as not built. The clearing happens
  *after* the preflight probe, so a misconfigured provider fails without
  touching a graph that still works.
- `ValueError: Graph Extraction failed. No entities detected` with a
  `failure_rate: 1.0` metrics block, and `1 validation error for
  LLMCompletionResponse / service_tier` in the log — a **provider response
  incompatibility**, not a model or quota problem. graphrag_llm types
  `service_tier` with OpenAI's exact enum; Groq returns `on_demand`, so every
  response was rejected after the HTTP call had already succeeded and spent
  tokens. Groundly widens that field (`allow_nonstandard_service_tier`), so
  this is fixed — but the shape is worth recognising: **100% failure with a
  successful-looking request count means graphrag couldn't parse the
  responses, not that the provider refused them.** Note the preflight probe
  cannot catch this class, since it validates through `llm/chat.py`, which
  reads litellm's own permissive response model.
- **The build looks stuck.** It isn't silent any more — `groundly index
  --graph` shows a bar advancing through graphrag's workflows. For the detail
  behind it, add `--debug` (or set `GROUNDLY_LOG_LEVEL=DEBUG`) to stream
  graphrag's own log lines to stderr; the bar is suppressed while logging is
  on so the two don't fight. graphrag also writes every build to
  `<subject>/graph/logs/indexing-engine.log`, which never leaves your machine
  (it's excluded from bundle export).
