# Configuring the GraphRAG provider

`groundly index --graph` builds the graph retrieval arm: entity/relation
extraction, Leiden community detection, and hierarchical summarization over
your indexed materials. Extraction is a real LLM call per chunk — this is the
one call class that needs a specific kind of provider, not just any
configured endpoint.

## Extraction needs a verified model, not necessarily a cloud one

LM Studio/Ollama work mechanically for `extraction` — it's the same
OpenAI-compatible `base_url`+`model`+`key` shape as every other call class,
and an unset key is fine (Groundly passes a placeholder graphrag's own
config validation requires, same as any local provider). **Cloud stays the
default recommendation** — it needs no per-model verification and nothing
below is required to make `--graph` work. But the old rule here — "never a
small local model" — was right about *small* and wrong about *local*.
Measured 2026-07-30 on an M1 Pro / 16 GB, Ollama 0.30.5, n=3 against a fixed
sample chunk
([full report](../superpowers/reviews/2026-07-30-local-extraction-feasibility.md)):

- **The measured floor: `gemma4:12b`, reasoning off,
  `graph.context_window` = 12288.** 42.2 s median per extraction call —
  **9.2× faster** than the same model with reasoning left on (386.5 s
  median) — with zero malformed records either way, and entity/relationship
  counts as good or better (9/7, 8/7, 8/7 reasoning-off vs. 7/6, 11/10, 7/6
  reasoning-on). Community reports, the one stage that genuinely needs
  structured output, held up too: 3/3 conforming in both arms, 4.4× faster
  (239.0 → 53.6 s median), and reasoning-off covered 7/7 input entities on
  all three samples against 6/7 on one reasoning-on sample.
- **The cautionary case: `qwen3.5:4b`.** Going smaller doesn't buy speed —
  it's **4.8× slower than the 12B** (201.8 s median) because it emits 3–24×
  more completion tokens (up to 12,959 against the 12B's 536), and it
  produced **1–44 malformed records per call** against the 12B's zero.
  Worse, `reasoning_effort: "none"` doesn't suppress its deliberation the
  way it does on gemma — it empties `reasoning_content`, but the thinking
  relocates into the content channel: one call returned 53,355 characters of
  "extraction output" that read, mid-stream, "Wait, re-evaluating based on
  strict entity types provided..." and ended "Final format check: ... Output
  starts now...". Community reports fared no better — one returned
  `findings: []` while still conforming to the schema. A build on this model
  completes, reports success, and hands back a garbled graph: graphrag drops
  malformed records silently, and Groundly's failure gate counts call
  failures, not dropped records.

So the reason to avoid a small local model is no longer mainly **time** —
with reasoning verified off, a mid-tier local model is competitive with
cloud on latency. For a model that's actually too small, like the 4B above,
the disqualifying reason is **quality**: it can't reliably stop, and its
failures don't surface as errors.

For context, the old (reasoning-on) baseline this guide used to quote:
measured 2026-07-27 on an M1 Pro / 16 GB, `gemma-4-12b-qat` at 16384 context
on LM Studio produced a perfectly good graph — 12 entities and 11
relationships from a sample chunk, zero malformed records, community reports
covering every supplied entity — but took **199.9 s per chunk**:

| corpus | serial | at LM Studio's default `--parallel 4`, optimistic |
| --- | --- | --- |
| 355 chunks | 19.7 h | 6.6 h |
| 1194 chunks | 66.3 h | **22.1 h** |

With reasoning verified off and `graph.context_window` at 12288 (Ollama,
extraction pass only, same optimistic parallel assumption):

| corpus | serial | at parallel-4 |
| --- | --- | --- |
| 355 chunks | 4.2 h | 1.4 h |
| 1194 chunks | 14.0 h | **4.7 h** |

Both tables cover the extraction pass only — community reports bill on top
either way. **Nothing here has been validated end-to-end**: these are
single-call measurements against one fixed chunk, excluding gleanings,
community reports, description summaries, and graphrag's own response
cache. Build one small subject and look at it before trusting a large run,
local or cloud.

If you don't want to deal with any of this — model choice, reasoning
verification, context sizing — go straight to a cloud provider, or skip
`--graph` entirely: the vector arm works with zero API key, and
`groundly index` (without `--graph`) is completely unaffected either way.

## Configure it

```sh
groundly config set extraction.base_url https://api.<provider>.com/v1
groundly config set extraction.model <mid-tier-model>
groundly config set extraction.key <your-api-key>
```

If `<mid-tier-model>` is in litellm's bundled price map (most mainstream cloud
models are), cost tracing works automatically — no extra config. That map ships
with litellm and can be months behind the provider's real rates, so every cost
line names where its prices came from; if yours are wrong, set both override
fields below and they win. For a local/unmapped model they're the only way to
get a dollar figure at all — without them `groundly index --graph` still prompts
for confirmation before building, it just can't price it:

```sh
groundly config set extraction.input_price_per_mtok <price per 1M input tokens>
groundly config set extraction.output_price_per_mtok <price per 1M output tokens>
```

`groundly config` shows the effective value (key masked) for every call
class, including `extraction`.

## Turn reasoning off before trusting a local model for extraction

```bash
groundly config set extraction.reasoning_effort none
```

`reasoning_effort` is settable under any call class the same way
(`chat.reasoning_effort`, etc.) — **it's a passthrough value, not
validated**; Groundly forwards whatever string you set. What it should be
depends on the provider: `"none"` for Ollama; OpenAI's o-series reasoning
models take `low`, `medium`, or `high` instead.

**Only `extraction` is measured, and the recommendation stops there.** The
9.2× above is an extraction result, and extraction is the one call class
whose job — emit a delimited tuple list — genuinely has nothing to reason
about. `chat` is a different task: it synthesises an answer *and* has to keep
citation discipline while doing it. Setting `chat.reasoning_effort=none` on
`gemma4:12b`, a broad "what is this course about" question came back with no
resolvable citations at all (a refusal, since zero citations is an error and
never a degraded answer), while narrow factoid questions answered fine with
citations on repeated runs. That is one model on a handful of questions, not
a characterised failure — but it is enough that you should not set it on
`chat` or `generation` without checking your own grounded answers first.

**Verify it with one call, per model — the setting doesn't mean the same
thing on every model.** On `gemma4:12b`, `"none"` genuinely suppresses
deliberation, which is what the numbers above depend on. On `qwen3.5:4b`,
the same setting empties `reasoning_content`, but the deliberation doesn't
go away — it relocates into the answer itself (see the cautionary case
above), which nothing here catches automatically. So an empty
`reasoning_content` is necessary but not sufficient: send one real
extraction prompt and check that `content` actually came back as the
delimited tuple format, not prose.

**Both local runtimes behave the same way here** (measured 2026-08-01):
`reasoning_effort: "none"` works, and the apparent alternative,
`chat_template_kwargs: {"enable_thinking": false}`, is silently *ignored* by
both. On `google/gemma-4-12b-qat` in LM Studio the switch took one trivial
answer from 48 completion tokens to 2; on `qwen/qwen3.5-9b` there, 98 to 2;
on `gemma4:12b` in Ollama, 68 to 2. So this setting is portable across LM
Studio and Ollama, which is not something to assume of provider-specific
parameters in general — verify it if you move to a third runtime.

Ollama leaves `completion_tokens_details` null in the response, so you can't
read reasoning-token counts directly there — the length of
`reasoning_content` is the only signal. LM Studio reports neither field once
reasoning is off, which is itself the confirmation you want.

## Build the graph

```sh
groundly index <SUBJECT> --graph
```

On first build, this prints a cost **range** and then asks for confirmation
(`--yes`/`-y` skips the prompt):

```
Estimated graph build: ~1,017,519 input tokens, up to ~3,448,272 output
  $0.061 to $0.682
  prices: litellm 1.86.2's bundled price map ('mistral/mistral-small-latest') — may be out of date
  extraction pass only — community reports and description summaries are billed on top, and cannot be sized before the graph exists
  ⚠ mistral-small-latest is a moving alias — it may now point at a differently-priced model than the one priced above.
```

Reading it:

- **Input tokens** are chunk text ÷ 4 **plus the ~696-token extraction preamble
  for every chunk**, which is usually the bulk of it (on a 1194-chunk corpus
  averaging 156 tokens per chunk, the preamble is most of every call).
- **The upper bound** assumes every call fills the room it has left to answer in
  — `graph.context_window` minus the preamble minus one full chunk. Real output
  is normally well under that, but *how far* under depends on the model, not on
  your corpus: on one 355-chunk build, output ran 0.87× the input on four runs
  and 4.06× on a fifth with a reasoning model. That's why it's a range.
- **Both ends cover the extraction pass only.** Community reports and
  description summaries are billed on top and genuinely can't be sized in
  advance — they're sized by the graph that doesn't exist yet, and the same
  corpus produced 23 communities on one build and 436 on another. Expect the
  real bill to land above the upper bound.
- **Prices** come from `extraction.input_price_per_mtok` /
  `output_price_per_mtok` if both are set, else litellm's bundled map for
  `extraction.model`, matched either bare (`gpt-4o-mini`) or by provider prefix
  (`groq/llama-3.3-70b-versatile`). The line always says which.
- **The moving-alias warning** appears for any model named `*-latest`. Those
  aliases get repointed at new models with new prices while the bundled map
  keeps the old ones — `mistral-small-latest` is priced at $0.06/$0.18 per Mtok
  by litellm 1.86.2 and costs $0.15/$0.60 today, so the range above is really
  $0.153 to $2.22. Pin the dated model id (`mistral-small-2603`), or set the
  override fields, if you want the estimate to mean something.

If the model is priced by neither source you'll see "no cost estimate
available" instead of dollar figures, but you still get the token counts and
you're still asked to confirm before anything is sent anywhere.

When the build finishes it prints what it **actually** spent, metered from
graphrag's own usage aggregates rather than re-estimated — cached responses
counted in the tokens but excluded from the cost, since a retry doesn't pay for
them twice.

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

A local model loaded at 4096 fails every single call, and graphrag *swallows*
those failures per chunk, so without the guards below you'd get hours of work
and an empty graph. It doesn't always announce itself as `Context size has been
exceeded` either: on a reasoning model the thinking tokens are charged to the
same budget, so the call returns HTTP 200 with an **empty body** and
`finish_reason: "length"` — measured at prompt 2356 + reasoning 1737 = exactly
4096, with three tokens left for the answer. Extraction at 4096 produced zero
entities on the same model that produced twelve at 16384.

Defaults differ sharply and neither is safe to assume: recent LM Studio loaded a
9B at 32768 unprompted, while Ollama sizes from VRAM and picked **4096** on the
same machine. Check with `lms ps` / `curl -s localhost:1234/api/v0/models`, or
`ollama ps`.

So Groundly scales every budget to one number:

```bash
groundly config set graph.context_window 8192
```

**This is a budget Groundly assumes, not a measurement of anything real** —
it feeds graphrag's own `prompt_budgets()`, and nothing checks it against
what the endpoint will actually accept. It goes wrong in two directions:

- **Set above the model's real window** — calls fail or truncate, exactly as
  above: `Context size has been exceeded`, or the silent empty-body case
  where reasoning tokens eat the budget before the answer does.
- **Set below the model's real window** — nothing fails; Groundly just asks
  for less than the model could give. Against a large cloud model this is
  the intended use: a conservative budget produces smaller, cheaper
  community summaries without touching correctness.

Which one applies depends entirely on what's actually loaded. Read the real
number back rather than trusting what you asked for — in LM Studio that's
the context length in the model's load settings, not its advertised
maximum. `curl -s localhost:1234/api/v0/models` reports the real
`loaded_context_length`, and that is the number to trust: on 2026-07-27
`lms load -c` was honoured on the GGUF/llama.cpp engine and **ignored on the
MLX engine**, which loaded one model at 32768 whether asked for 4096 or
16384. That may no longer hold — on 2026-08-01 `lms load -c 12288` was
honoured on an MLX model, reading back exactly 12288. One value on one model
is not enough to call the earlier finding stale, which is precisely why the
advice is to read it back rather than to trust any rule about which engine
respects the flag.

**For a local model, set `graph.context_window` to that real number** —
there's no cloud-scale headroom to budget conservatively against, so the
setting should describe what's actually loaded. At that point, the shipped
default of 4096 is not "works but smaller": the stock `community_report`
template alone is 2,214 tokens, and the budget `prompt_budgets()` derives
from a 4096 window allows only 2,048 more of packed context —
**4,262 input tokens against a 4,096 window**, before a single output token.
A local model loaded at 4096 cannot produce a community report at all; that
isn't a quality tradeoff, it's arithmetic.

**12288, not 16384, is the local recommendation.** Running the real
`prompt_budgets()` against the measured 2,214-token template (full
derivation in
[2026-07-30-local-extraction-feasibility.md](../superpowers/reviews/2026-07-30-local-extraction-feasibility.md)):

| `graph.context_window` | gleanings | extraction calls/chunk | report total | fits? |
| --- | --- | --- | --- | --- |
| 4096 | 0 | 1 | 5,286 | no — over by 1,190 |
| 6144 | 0 | 1 | 6,822 | no — over by 678 |
| 8192 | 0 | 1 | 8,310 | no — over by 118 |
| **12288** | **0** | **1** | 10,358 | **yes** |
| 16384 | 1 | **2** | 12,214 | yes |

`max_gleanings` flips from 0 to 1 exactly at 16384, and a gleaning round is a
**second full LLM call per chunk** — on the stage that already accounts for
nearly all call volume. 16384 does reproduce stock graphrag exactly,
including the gleaning pass, which is the right choice if you're paying a
cloud provider for that fidelity. For a local model, that fidelity is a
doubled extraction bill for no measured gain: 12288 fits a community report
with room to spare and keeps extraction at one call.

Raising the model's context past 12288 is still worth it if your hardware
has room — more context means richer community reports, which is most of
what the global arm answers from — just know that 16384 is where that
richness starts costing a second extraction call per chunk, not a free
upgrade.

## Community reports need JSON-schema structured output — by default, that's the extraction model

Community reports — what global search and `overview` answer from — are the one
stage graphrag requests structured output for. It passes a Pydantic model, which
litellm turns into `response_format: {"type": "json_schema", …, "strict": true}`.

**If your extraction model can't clear this bar, you don't have to replace it** —
`graph.report_call_class` (default `"extraction"`; see "Routing community reports
to a different provider" below) sends just the 23–436 community-report calls to a
different configured call class, while extraction itself sends no `response_format`
at all — it's plain delimited text. Everything below describes what happens when
the model actually serving reports doesn't support the schema, whichever call
class that is.

**This is stricter than "JSON mode".** The older `{"type": "json_object"}` is a
different capability, and endpoints disagree about which they accept — in both
directions. Measured 2026-07-26:

| endpoint | model | `json_object` | `json_schema` |
| --- | --- | --- | --- |
| api.deepseek.com | `deepseek-v4-flash` | yes | **no** |
| api.deepseek.com | `deepseek-v4-pro` | yes | **no** |
| api.deepseek.com | `deepseek-chat`, `deepseek-reasoner` | yes | **no** |
| LM Studio | any model, either engine | **no** | yes |
| Ollama | any model | yes | yes |

So DeepSeek cannot build a graph today, whichever model you pick — it answers
`This response_format type is unavailable now`. LM Studio is the exact inverse
and refuses `json_object` with `'response_format.type' must be 'json_schema' or
'text'`. Ollama accepts both — note the two local runtimes disagree here, so
"local" is not one behaviour.

Support is a property of the **model**, not the provider — on Groq,
`llama-3.3-70b-versatile` has no schema support while `openai/gpt-oss-120b` and
`llama-4-scout` do. Don't reason about it one provider at a time.

Nor can you trust a capability table, litellm's included: its bundled map marks
`deepseek-chat` as supporting response schemas, and the live API rejects it. The
preflight probe is the more reliable answer, which is why it sends graphrag's own
response model rather than an approximation of it — a model that outright refuses
this fails in seconds instead of after the whole extraction pass. Note "outright
refuses": the probe catches a *rejected request*, and nothing more. See the local
section below for two ways a model accepts it and still builds you an empty graph.

Left unchecked, the failure is confusing: every report call fails, graphrag is
left with an empty reports table, and it dies merging that table on a column an
empty frame doesn't have — `KeyError: 'community'`, tens of minutes into a run
whose extraction stage worked perfectly. That is exactly what happened on
2026-07-26 — 679s and 2.96M tokens of flawless extraction, then 436 failed
reports — because the probe was checking `json_object`, which DeepSeek accepts.

### On a local runtime, "accepts json_schema" is close to meaningless

LM Studio and Ollama enforce the schema by constrained decoding in the *server*,
so essentially everything accepts the request. Acceptance is therefore not a
useful filter locally, and the failures move somewhere the table above cannot
show. Measured 2026-07-27 on an M1 Pro / 16 GB
([full report](../superpowers/reviews/2026-07-27-local-json-schema-capability.md)):

| runtime | model | engine | loaded ctx | usable report? |
| --- | --- | --- | --- | --- |
| LM Studio | `google/gemma-4-12b-qat` | gguf | 16384 | **yes — 3/3** |
| Ollama | `gemma4:12b` | gguf | 16384 | **yes — 3/3** |
| LM Studio | `google/gemma-4-12b-qat` | gguf | 4096 | no — 0/3, answer truncated away |
| Ollama | `gemma4:12b` | gguf | 4096 *(its default)* | no — 0/3, 0 entities extracted |
| LM Studio | `qwen/qwen3.5-9b` | mlx | 32768 | **no — 0/3, empty response body** |
| LM Studio | `google/gemma-4-12b` | mlx | 19456 | no — 2/3 had zero findings |

Two configurations in six, and both are the same gemma-class model at 16384.
Every failure was silent. The two ways a model accepts the request and still
gives you nothing:

- **The answer goes to the wrong channel.** `qwen/qwen3.5-9b` returns HTTP 200
  with `content: ""` on *every* structured-output call — `finish_reason: "stop"`,
  29k tokens to spare, and the complete report sitting in `reasoning_content`
  instead. The schema grammar constrains the content channel only, so a model
  whose template never exits its thinking channel never fills it. **Do not use
  `qwen/qwen3.5-9b` for `extraction`** — it is fine for `chat`/`generation`,
  which never request structured output.
- **The report is valid and empty.** `google/gemma-4-12b` (MLX) returned
  `"findings": []` in 2 of 3 samples — schema-valid, parses fine, and `findings`
  is where the entire body of a community report lives. Nothing errors; the
  graph just gets thinner.

**On Ollama, the default is the failing one.** It sizes context from VRAM and
says so at startup — `vram-based default context … default_num_ctx=4096` — and
at 4096 the run above extracted **zero entities**. LM Studio, by contrast,
loaded a 9B at 32768 unprompted. Worse, `graph.context_window` cannot fix it:
that setting tells Groundly what budget to *assume*, and Ollama's context is
set server-side only —

```bash
OLLAMA_CONTEXT_LENGTH=16384 ollama serve   # or PARAMETER num_ctx in a Modelfile
```

— because the OpenAI-compatible `/v1/chat/completions` route Groundly calls has
no per-request field for it. Check what you actually got with `ollama ps`; the
`CONTEXT` column is the real number. Set the server first, then match
`graph.context_window` to it.

Both channel failures above are per **model and build**, not per engine: the
same gemma weights spent 1737 tokens on reasoning as a GGUF build and 0 as an
MLX build. Reasoning tokens
also count against the context window, which is what kills the 4096 row above —
prompt 2356 + reasoning 1737 = exactly 4096, and three tokens reached the answer.

**The preflight probe does not catch either of these.** It sends a 24-token
prompt and only checks that no exception was raised — not that a response body
came back. A model that fails the way `qwen/qwen3.5-9b` does passes preflight and
then produces an empty report for every community, which surfaces as the same
`KeyError: 'community'` after the whole extraction pass has been paid for.
If you use a local model for `extraction`, build one small subject first and look
at the community reports before trusting a large run.

### Routing community reports to a different provider

Community reports are the one stage that requires structured output, and —
per the two local failure modes above — the stage where a local model most
often fails silently even after "accepting" the schema.
`graph.report_call_class` names which configured call class actually serves
community reports, independent of `extraction`:

```bash
groundly config set graph.report_call_class chat
```

Default is `"extraction"` — reports go to the same provider as everything
else in this guide, which is fine if that provider passed the JSON-schema
checks above. Setting it to `"chat"` (or any other configured call class)
routes just the 23–436 community-report calls to that provider's config,
while the far larger extraction pass — up to 1,194 calls on the reference
corpus — keeps running wherever you've pointed `extraction`, local included.
This is the practical way to keep a local build cheap on the stage with all
the call volume, and pay for schema support only on the stage that actually
needs it.

**This routes the build only.** Query-time global search — the `overview`
tool, and any `ask` the router sends down the global arm — makes its own
synthesis call through the `extraction` provider, not `report_call_class`
(`retrieval/graph.py`'s query-time completion config is always `extraction`,
for both local and global search). So if you set `report_call_class` because
your `extraction` model can't do structured output, `overview` still calls
that same model — a different, non-schema-gated call, but still the model
you moved reports away from. Current limitation, not something you can
configure around today.

**A free local extraction model can blank the whole build's cost line.**
`metered_usage()` sums tokens across both models regardless, but prices the
whole build with one shared flag: if either model's price can't be resolved
(no litellm entry, no manual override), the printed figure is the token
counts with no dollar amount at all — even though the other model is
priced. If `extraction` is local/unpriced and `report_call_class` points at
a paid provider, set `extraction.input_price_per_mtok = 0` and
`extraction.output_price_per_mtok = 0` explicitly (rather than leaving them
unset) so the local model prices at $0 instead of unknown, and the paid
report model's real cost still prints.

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
- **The probe passed, extraction worked, and every community report is empty.**
  A local-model failure the preflight check cannot see: the model accepts the
  structured-output request and answers it with an empty body (or with
  `findings: []`). Measured on `qwen/qwen3.5-9b`, whose answer lands in
  `reasoning_content` instead of `content`. Confirm with one call —
  `curl -s localhost:1234/v1/chat/completions -d '{"model":"…","messages":[…],
  "response_format":{"type":"json_schema",…}}'` — and check whether
  `choices[0].message.content` is `""` while `reasoning_content` holds the
  answer. If so, that model cannot do `extraction`; it is still fine for
  `chat`/`generation`.
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
