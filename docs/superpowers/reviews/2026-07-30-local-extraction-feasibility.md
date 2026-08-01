# Local extraction is throughput-bound on reasoning tokens, not on model size

Measured 2026-07-30 on **M1 Pro, 16 GB unified memory, Ollama 0.30.5** — same machine as
[2026-07-27](2026-07-27-local-json-schema-capability.md), so the numbers are comparable.
Reproduction harness in the appendix.

## Why this was measured

`docs/guides/graphrag-provider.md:9` says *"extraction needs a mid-tier cloud model, never a
small local model"*, and [the 2026-07-27 review](2026-07-27-local-json-schema-capability.md)
backs it with throughput: 199.9 s per extraction call (LM Studio, `gemma-4-12b-qat` @16384),
269.2 s on Ollama, → 66.3 h serial for the 1194-chunk reference corpus.

Reading graphrag 3.1.0's pipeline against Groundly's use of it, that conclusion is **bound to
the configuration it was measured in**. Three variables were never moved:

1. **Reasoning was left on** for a task that needs none.
2. **Only 9B–12B models were tried** (the two that fit 16 GB).
3. **One model served all three LLM stages**, so the stage with the hardest requirement
   (community reports) set the bar for the stage with 1,194× the call volume (extraction).

## Summary

| # | Finding | Severity |
| --- | --- | --- |
| 1 | Disabling reasoning cuts the extraction call from **269.2 s to ~51 s** on the *same* model, runtime and context — and *improves* extraction output. The 2026-07-27 throughput verdict measured a reasoning tax, not a model-size limit. | **critical** |
| 2 | **Going smaller does not work.** `qwen3.5:4b` is 4.8× *slower* than the 12B (it emits 3–24× more tokens) and produces 1–44 malformed records per call against the 12B's zero. `reasoning_effort: "none"` relocates its deliberation into the content channel instead of suppressing it. Nothing in graphrag or Groundly catches this. | **critical** |
| 3 | `graph.context_window` is defined two incompatible ways on one page — "set it to your model's real window" (local) vs. "4096 is a conservative budget, just smaller summaries" (cloud). Only the first reading makes 4096 arithmetically impossible for a community report (2,214-token template + 2,048 budgeted context = 4,262 against 4,096); the second is accurate as written. | medium |
| 4 | Extraction sends **no `response_format`** — it is delimited text. json_schema capability, which the guide screens models on, gates only the 23–436 community reports, not the 1,194 extraction calls. | **high** |
| 5 | Groundly cannot disable reasoning: `completion_model_config()` never sets `call_args`, graphrag's documented passthrough into litellm. Server-side is the only route today. **Since implemented** — decision 24 (2026-07-31) adds `providers.*.reasoning_effort`. | **high** |
| 6 | graphrag supports a different model per workflow (`completion_model_id` on every workflow config); Groundly registers one model under the default key, so all three stages share it. | medium |
| 7 | On Ollama, `reasoning_effort: "none"` works; `chat_template_kwargs {"enable_thinking": false}` is silently ignored. Ollama also leaves `completion_tokens_details` null, so reasoning tokens must be measured via `reasoning_content`. | medium |
| 8 | `graph.context_window = 16384` turns gleanings on, which makes extraction **two** LLM calls per chunk. **12288** is the only value that both fits a community report and keeps extraction at one call — the guide recommends 16384. | **high** |
| 9 | `gemma4:4b` does not exist — Gemma 4's smallest release is 12B. The 4B-class candidate on Ollama is `qwen3.5:4b`. | informational |

## What the extraction path actually demands

graphrag 3.1.0 runs three LLM stages under Groundly's config. Their requirements are
**disjoint**, which is the fact the single-model setup obscures:

| Stage | Calls (1194-chunk corpus) | Output contract | Input | Failure mode |
| --- | --- | --- | --- | --- |
| `extract_graph` | **1,194** | delimited text `<\|>` / `##` / `<\|COMPLETE\|>`, **no `response_format`** | 695 preamble + ≤512 chunk | malformed records silently dropped; >5% chunk failures → build refused |
| `summarize_descriptions` | per duplicated description | plain text | ≤ min(4000, cw/2) | low risk |
| `create_community_reports` | 23–436 | **json_schema strict**, nested `$defs`/`$ref` | 2,214 template + ≤ min(8000, cw/2) | empty `content`; schema-valid `findings: []` |

The parser is pure string splitting (`graph_extractor.py:135-171`) against hardcoded
delimiters — `TUPLE_DELIMITER = "<|>"`, `RECORD_DELIMITER = "##"`,
`COMPLETION_DELIMITER = "<|COMPLETE|>"`. A record that fails `len(record_attributes) >= 4` is
dropped without raising. Nothing in that path benefits from a reasoning pass.

Both local failures recorded on 2026-07-27 (`qwen3.5-9b`'s empty body, `gemma-4-12b` MLX's
`findings: []`) were **community-report** failures. Extraction output was clean on every model
that ran, at every context size that fit.

### Upstream's position (microsoft.github.io/graphrag)

- Tested on the gpt-4 series; models **must** support "structured outputs adhering to a JSON
  schema", stated as a hard requirement.
- Local models are acknowledged only via Ollama / LiteLLM proxy, with the caveat *"we
  frequently see issues with malformed responses (especially JSON)"*.
- **No minimum context window is documented.** Groundly's ~16k figure is derived from stage
  defaults, not upstream guidance.

Worth noting the upstream requirement is stated for the pipeline as a whole. Per stage it is
only true of community reports.

## Results

All rows: Ollama, `num_ctx` 16384 verified via `ollama ps`, n=3 against the fixed sample.
`reason_chars` is the length of `reasoning_content` — Ollama leaves
`completion_tokens_details` null, so reasoning tokens are not directly reported.

### Finding 1 — the reasoning tax is the whole story

`gemma4:12b`, identical model / runtime / context, reasoning the only variable:

| arm | extraction s (n=3) | median | ent / rel | malformed | compl. tokens | reason_chars |
| --- | --- | --- | --- | --- | --- | --- |
| think **ON** | 386.5 / 536.7 / 368.6 | **386.5** | 7/6, 11/10, 7/6 | 0 | 4574 / 6876 / 4939 | 13.9k / 21.0k / 16.1k |
| think **OFF** | 47.5 / 36.2 / 42.2 | **42.2** | 9/7, 8/7, 8/7 | 0 | 536 / 490 / 572 | 0 / 0 / 0 |

**9.2× faster, and the output is no worse** — median 8 entities with reasoning off against 7
with it on, zero malformed records in all six calls.

The latency ratio and the token ratio are the same number: median completion tokens fall
4,939 → 536, a factor of 9.2, against a wall-clock factor of 9.2. Decode-bound, exactly as
expected on unified memory — **the model was not thinking its way to a better graph, it was
paying nine tokens of deliberation for every token of answer graphrag keeps.** The extraction
prompt asks for a delimited tuple list; there is nothing in it to reason about.

Community reports, same two arms:

| arm | report s (n=3) | conforms | findings | entities covered | rating |
| --- | --- | --- | --- | --- | --- |
| think **ON** | 285.8 / 198.5 / 239.0 | 3/3 | 6, 5, 5 | 7/7, **6/7**, 7/7 | 8.5, 7.0, 7.0 |
| think **OFF** | 67.6 / 53.6 / 46.7 | 3/3 | 5, 5, 5 | **7/7, 7/7, 7/7** | 8.5, 6.5, 7.0 |

Reasoning off did **not** degrade the one stage that genuinely needs structured output: 4.4×
faster, still 3/3 conforming, and coverage was actually more consistent. The rating instability
the 2026-07-27 review flagged (identical input scoring 4.0/4.0/8.0) persists in both arms
(8.5/7.0/7.0 and 8.5/6.5/7.0) — that is a model property, unrelated to reasoning.

### Finding 2 — going smaller does not help; it fails in both directions at once

The obvious next move — take the 9.2× win and spend it on a smaller, faster model — **does not
work**. `qwen3.5:4b`, same runtime, same 16384 context, same fixed sample:

| arm | extraction s (n=3) | median | ent / rel | **malformed** | compl. tokens |
| --- | --- | --- | --- | --- | --- |
| 4B think ON | 418.2 / 419.2 / 437.3 | 419.2 | 8/2, 17/8, 4/0 | **26 / 14 / 1** | 13040 / 13262 / 13801 |
| 4B think OFF | 411.4 / 201.8 / 50.2 | 201.8 | 15/3, 14/2, 10/6 | **44 / 1 / 4** | 12959 / 6565 / 1663 |
| *12B think OFF, for reference* | *47.5 / 36.2 / 42.2* | *42.2* | *9/7, 8/7, 8/7* | ***0 / 0 / 0*** | *536 / 490 / 572* |

The 4B is **4.8× slower than the 12B**, not faster — because it emits 3–24× more tokens
(median 6,565 against 536). On decode-bound hardware, tokens are time, and a small model's
failure to stop costs far more than its smaller weight matrix saves.

And `reasoning_effort: "none"` does not do the same thing here that it does on gemma. It zeroes
`reasoning_content`, but the deliberation simply **relocates into the content channel**. From
the worst call (53,355 characters of "extraction output", `finish_reason: stop` — it was not
truncated):

> `("entity"<|>WORKER<|>person/role<|`Individual unit of processing…` … Wait, re-evaluating
> based on strict entity types provided. 'Person' might be too specific…`

…and it ends, after 44 malformed records, with:

> `Final format check: … I will replicate exactly. Output starts now...`

It invents types outside the supplied list (`person/role`), injects markdown and HTML into
descriptions (`<li>`, `<strong>`, backticks), and breaks the tuple format itself (`<|` for
`<|>`). This is the mirror image of the 2026-07-27 finding 1 — same model family, opposite
direction: there a reasoning model stranded its *answer* in `reasoning_content`; here
suppressing `reasoning_content` strands its *reasoning* in the answer. One root cause, a chat
template whose thinking is not cleanly separable from its output.

**Nothing catches this.** graphrag's parser drops a malformed record silently
(`graph_extractor.py:135-171`), and Groundly's 5% gate counts *call* failures, not dropped
records. A 4B build would complete, report success, and hand you a graph with 15 entities and 3
relationships where the 12B found 8 and 7 — entity-rich, relationship-poor, and salted with
markdown artefacts. Community reports show the same rot: one returned `findings: []` while
conforming, the 2026-07-27 finding-8 failure mode, and ratings collapsed to 2.5–2.5–6.0 against
the 12B's 8.5–6.5–7.0.

**So the guide's original claim survives after all — for the model class it never named.** A
*small* local model does garble the graph. A 12B with reasoning off does not.

### What that does to build time

Extraction pass only, from the measured medians. The `context_window` column matters because
of finding 8: at 12288 a chunk costs one call, at 16384 it costs two. Both arms were *measured*
at 16384; the 12288 rows reuse the same per-call latency, which is sound because the extraction
prompt does not scale with `context_window` — only `max_gleanings` does.

| model / arm | `cw` | s per chunk | 355 chunks | 1194 chunks |
| --- | --- | --- | --- | --- |
| 12B think ON | 12288 | 386.5 | 38.1 h → 12.7 h | 128.2 h → **42.7 h** |
| 12B think ON | 16384 | 773 (2 calls) | 76.2 h → 25.4 h | 256.4 h → 85.5 h |
| 12B think **OFF** | 16384 | 84.4 (2 calls) | 8.3 h → 2.8 h | 28.0 h → 9.3 h |
| **12B think OFF** | **12288** | **42.2** | 4.2 h → **1.4 h** | 14.0 h → **4.7 h** |

Second figure in each cell is at `--parallel 4`, dividing by 3 — the prior review's optimistic
assumption, carried over unmeasured. The two-call rows price the gleaning round at the same
cost as the first, which is rough in both directions: its prompt is larger (it replays the
conversation) but its output is usually shorter.

Read the last row against the guide's current advice. The published number for this corpus is
**22.1 h at parallel-4**; reasoning off at 12288 puts it at **4.7 h** — and the 355-chunk
subject at **1.4 h**, which is a coffee break rather than a weekend.

**Against the published baseline.** The 2026-07-27 review recorded 269.2 s and 7 ent / 5 rel
for this model/runtime/context with reasoning on. Output reproduces closely (7/6 twice). The
latency does not match tightly — 269.2 s sits just below this run's 368.6–536.7 s range — but
that figure was a single call with no variance estimate, and the spread *within* this arm is
itself 1.46×. The A/B above is unaffected either way: both arms ran in the same session,
against the same loaded model, minutes apart.

## Finding 3 — `graph.context_window` is defined two incompatible ways; only one of them makes 4096 impossible

Measured stock prompt sizes (graphrag 3.1.0):

| prompt | est. tokens |
| --- | --- |
| `community_report` | **2,214** |
| `extract_graph` (upstream) | 1,620 |
| `extract_graph` (Groundly bundled) | 695 |
| `summarize_descriptions` | 183 |

At `graph.context_window = 4096`, `prompt_budgets()` yields
`community_max_input_length = min(8000, 4096 // 2) = 2048`. Template + packed context ≈
**4,262 input tokens against a 4,096 window** — before a single output token, and before the
1,024 the same budget requests back.

This holds under either reading of `max_input_length`: if it is context-only, the total is
4,262; if it is the whole input budget, 2,048 is already below the 2,214-token template.

**This is conditional, not the universal claim an earlier version of this finding made.**
`graph.context_window` is a budget Groundly assumes, not a measurement of anything real. Against
a 128k-context cloud model with the setting left at its 4096 default, the arithmetic above is
completely harmless — 2,214 + 2,048 = 4,262 tokens is a sliver of a 128,000-token window, and
`docs/guides/graphrag-provider.md:200`'s *"works out of the box but produces smaller community
summaries (weaker `overview`/global search)"* is an accurate description of exactly that case.
The arithmetic only bites when 4096 is *also* the real ceiling — which is what a local model at
its default context actually is, and what the same page's own advice
(`docs/guides/graphrag-provider.md:193`, *"set it to whatever your model is actually loaded
with"*) tells a local user to make it.

The actual defect is that the page states both readings on one screen without saying which
applies when: line 193 treats `graph.context_window` as *the model's real window*; line 200
treats the 4096 default as *a conservative budget against a much bigger one*. Both are
legitimate uses of the same knob, but only the second can be "smaller but working" — for a
local user following line 193's own instruction, 4096 isn't smaller, it's zero, which the 0/3
conformance measured at 4096 on 2026-07-27 already showed without needing the reasoning-token
account at all.

### The constructive corollary — 12288 is the local sweet spot

`prompt_budgets()` moves two things with `context_window`, and they pull in opposite
directions: community-report budgets scale *up* with it, while `max_gleanings` flips from 0 to
1 at exactly 16384. Gleanings are not free — `max_gleanings=1` makes `_process_document` issue
**two** LLM calls per chunk (the initial extraction plus one `CONTINUE_PROMPT` round; the
`LOOP_PROMPT` round is skipped by the loop's `i >= max_gleanings - 1` early break), replaying
the accumulated conversation in the second.

Running the real `prompt_budgets()` against the measured 2,214-token template:

| `graph.context_window` | gleanings | extraction calls/chunk | report input | report total | fits? |
| --- | --- | --- | --- | --- | --- |
| 4096 | 0 | 1 | 4,262 | 5,286 | **no — over by 1,190** |
| 6144 | 0 | 1 | 5,286 | 6,822 | **no — over by 678** |
| 8192 | 0 | 1 | 6,310 | 8,310 | **no — over by 118** |
| **12288** | **0** | **1** | 8,358 | 10,358 | **yes** |
| 16384 | 1 | **2** | 10,214 | 12,214 | yes |
| 32768 | 1 | 2 | 10,214 | 12,214 | yes |

**12288 is the only value that both fits community reports and keeps extraction at one call per
chunk.** The guide currently recommends 16384 on the grounds that it "reproduces stock graphrag
exactly" (`graphrag-provider.md:202`) — true, and for a local model that fidelity costs a
doubled extraction bill on the stage that dominates the build.

The extraction prompt itself does not scale with `context_window` (it is 695 preamble + chunk
regardless), so per-call latency at 12288 is identical to 16384. The saving is purely the
second call.

## Finding 5 — Groundly has no way to switch reasoning off

**Since implemented:** decision 24 (2026-07-31) added `providers.*.reasoning_effort`,
forwarded as `extra_body`, closing exactly the gap this finding describes. What follows
is the gap as measured on 2026-07-30, before that change.

`completion_model_config()` (`groundly/llm/graphrag_adapter.py:143-155`) builds graphrag's
`ModelConfig` without `call_args`. That field defaults to `{}` and graphrag splats it directly
into the litellm call:

```python
# graphrag_llm/completion/lite_llm_completion.py:243
**model_config.call_args,
```

So it is the supported passthrough for `reasoning_effort`, `chat_template_kwargs`,
`temperature`, and `max_tokens` — and Groundly leaves it empty. `groundly/llm/chat.py:41`
likewise accepts only `response_format`, with no `**kwargs`.

Consequence: the single highest-leverage setting for local extraction is unreachable from
`config.toml`. It can only be set server-side — in a Modelfile, or at LM Studio load time.

## Follow-up 2026-08-01 — validated end-to-end

Everything above is single-call measurement. A real `groundly index --graph` was then run on
a fresh 89-chunk subject (the `IC` corpus, 8 lecture PDFs), `gemma4:12b` via Ollama at
`num_ctx` 12288 with `reasoning_effort: "none"`:

| | predicted above | measured in a real build |
| --- | --- | --- |
| seconds per chunk | 42.2 (bench median) | **40.2** (59.6 min wall clock for 89 chunks) |

**The extrapolation held.** Extraction ran 12:37:55 → 13:37:31; the bench figure was within
5% of the pipeline rate.

Output quality, which the bench could only sample:

- **385 entities, 341 relationships** from 89 chunks
- **43 communities, 43 community reports** — no report call failed
- **0 reports with empty `findings`** (min 2, max 8, mean 4.8) — the schema-valid-and-empty
  failure mode that disqualified the 4B and one MLX build did not appear
- `ask` returns grounded answers whose citations resolve to real document pages

Metered spend: **202,159 prompt / 72,420 completion** tokens — a completion:prompt ratio of
**0.36:1**, against the 4.06:1 decision 23 recorded for a reasoning model. That is the same
saving the bench measured as wall clock, showing up as tokens on a full build.

Two caveats this run also settled:

- The pre-build estimate (72,792 input) undershot metered input **2.8×**, exactly as the
  estimate's own disclaimer says it will — it sizes the extraction pass only, and the metered
  figure includes community reports and description summaries.
- **The reasoning result does not transfer to `chat`.** With `chat.reasoning_effort=none` on
  the same model, a broad "what is this course about" question produced no resolvable
  citations (correctly refused), while narrow factoid questions answered fine on repeated
  runs. Extraction emits a delimited tuple list and has nothing to reason about; answer
  synthesis has to hold citation discipline while composing. One model, a handful of
  questions — not characterised, but enough to scope the recommendation to `extraction`.

## The answer to "which local small models are capable"

**None of the small ones. One mid-sized one, configured correctly.**

The measured configuration that works on 16 GB:

```bash
# Ollama's context is server-wide on the OpenAI route, so bake it into a derived tag:
printf 'FROM gemma4:12b\nPARAMETER num_ctx 12288\n' > Modelfile
ollama create gemma4-12b-local -f Modelfile
ollama ps                      # confirm CONTEXT reads 12288

groundly config set extraction.base_url http://localhost:11434/v1
groundly config set extraction.model gemma4-12b-local
groundly config set graph.context_window 12288
groundly config set extraction.reasoning_effort none
```

…plus reasoning disabled, which **could not be set from Groundly at measurement time**
(finding 5) — the last line above needed a Modelfile carrying the model's own
thinking-off switch instead. **Since implemented:** decision 24 (2026-07-31) added
`providers.*.reasoning_effort` as a direct `config.toml` passthrough, shown above. It
still must be verified per model — `reasoning_effort: "none"` genuinely suppresses
deliberation on gemma and merely relocates it on qwen.

Two variables, moving in opposite directions:

| variable | effect on throughput | effect on quality |
| --- | --- | --- |
| reasoning **off** | **9.2× faster** | neutral to better (8 vs 7 entities, 0 malformed both) |
| model **smaller** (12B→4B) | **4.8× slower** | collapses (1–44 malformed, relationships halved) |

The intuition that a smaller model buys speed is wrong on this workload, because the cost is
tokens generated, not parameters resident — and small models are markedly worse at stopping.
This also means the shortlist is short: on 16 GB, a ~12B at Q4 with reliable thinking-off is
close to the only thing that qualifies, and `gpt-oss-20b` (15.78 GiB) still does not fit.

**Community reports remain the weak stage.** They conformed 3/3 here with reasoning off, but
they are where every previously recorded local failure happened, they are only 2–27% of the
calls, and graphrag already supports pointing them at a different model (finding 6). If one
stage should go to a cloud provider, it is this one — not extraction.

## What follows from this

Ordered by how much they change, and how cheap they are.

1. **Reconcile the `graph.context_window` claim** (`graphrag-provider.md:193` vs `:200`). The
   page defines the setting two incompatible ways — a local user's real window vs. a cloud
   user's conservative budget — without saying which applies when. State both: for a local
   model whose real window is 4096, the default can't produce a community report at all; for a
   cloud model where 4096 is a budget well under the real window, the existing "smaller
   summaries" framing already holds.
2. **Recommend 12288, not 16384, for local extraction** (`graphrag-provider.md:202`). Same
   report quality, half the extraction calls.
3. **Pass `call_args` through** from config, so `reasoning_effort` is reachable without a
   Modelfile. This is the single highest-leverage local setting and it is currently
   unreachable from `config.toml`. Small change in `completion_model_config()`.
   **Implemented in decision 24 (2026-07-31)** as `providers.*.reasoning_effort`.
4. **Consider a second completion model for community reports.** graphrag already supports it
   (`completion_model_id` per workflow); this would let the 1,194 plain-text extraction calls
   run locally while the 23–436 schema calls go to a provider that is known to fill them. This
   is the change that would actually make "local graph build" a supported configuration rather
   than a gamble. **Implemented in decision 24 (2026-07-31)** as `graph.report_call_class`.
5. **Re-qualify the guide's rule by model class, not by "local".** "Never a small local model"
   turns out to be right about *small* and wrong about *local*: a 4B garbles the graph exactly
   as the guide warns, while a 12B with reasoning off produces a clean one in a quarter of the
   time the guide quotes. The rule to state is a floor (~12B, thinking off, verified) plus the
   stage split, not a blanket prohibition.
6. **Add a malformed-record signal.** Nothing currently surfaces the 4B failure: graphrag drops
   bad records silently and Groundly's 5% gate counts call failures, not dropped records. A
   count of unparsed records per build, printed alongside "Graph built", would have made this
   failure self-evident instead of requiring a benchmark to find.

`docs/guides/lm-studio.md:13` also still recommends qwen generically; that predates both the
2026-07-27 finding that `qwen3.5-9b` cannot do `extraction` in LM Studio and this review's
finding that `qwen3.5:4b` cannot do it on Ollama either, for an unrelated reason.

## Limits of this measurement

- **One fixed chunk, n=3.** Format-following was measured on a single representative sample
  (~145 tokens, close to the 156-token corpus average), not across a real corpus. The 5%
  malformed-record gate could still trip on harder or shorter chunks. Nothing here is a
  substitute for one real `groundly index --graph` on a small subject.
- **Single-call extrapolation.** Projected hours multiply one extraction call by the chunk
  count. They exclude community reports, `summarize_descriptions`, and graphrag's response
  cache, and at 16384 they exclude the second (gleaning) call. Same basis as the 2026-07-27
  figures, so the comparison is fair, but the absolute number is a floor, not an estimate.
- **The parallel-4 column divides by 3**, carried over from the prior review's optimistic
  assumption. Not measured here. graphrag defaults to 25 concurrent requests, which a single
  local runtime will queue rather than absorb.
- **Ollama only.** LM Studio had no LLM installed on this machine at the time of measurement,
  so the runtime split from 2026-07-27 was not re-tested. `reasoning_effort` vs
  `chat_template_kwargs` is known to differ between them, so the reasoning-off result does
  **not** transfer to LM Studio unverified.
- **Two models, one family each.** `gemma4:12b` and `qwen3.5:4b`. The claim that ~12B is the
  floor is an inference from two points, not a swept curve — a 7–9B with reliable thinking-off
  was not tested and is the obvious gap.
- **The 4B's variance is extreme** (50.2–411.4 s within one arm) because output length is
  unbounded, so its median is a weak statistic. The quality verdict does not rest on it — 1–44
  malformed records against the 12B's 0/0/0/0/0/0 is categorical.
- `~/.groundly/` was never written to and no subject was indexed **by the single-call
  bench above** (2026-07-30) — see "Follow-up 2026-08-01" above, which closes this gap
  with a real 89-chunk `groundly index --graph` build.

## Appendix — reproduction harness

Context was set with a derived Ollama tag rather than by restarting the server, since
`OLLAMA_CONTEXT_LENGTH` is server-wide and the OpenAI route has no per-request field:

```bash
printf 'FROM gemma4:12b\nPARAMETER num_ctx 16384\n' > Modelfile.gemma12b
ollama create gemma4-12b-16k -f Modelfile.gemma12b
ollama ps   # CONTEXT column must read 16384
```

Then per arm:

```bash
python probe_reasoning.py http://localhost:11434/v1 gemma4-12b-16k "12b-think-ON"  -n 3
python probe_reasoning.py http://localhost:11434/v1 gemma4-12b-16k "12b-think-OFF" -n 3 --no-think
```

The harness extends the [2026-07-27 appendix](2026-07-27-local-json-schema-capability.md),
reusing its `COMMUNITY_INPUT` and `SAMPLE_TEXT` verbatim so T2/T3 stay comparable. It differs
in one way: the prior harness went through `groundly.llm.chat.complete()`, which returns
`content` only and takes no extra kwargs, but reasoning is the variable under test here — so
this one issues direct POSTs to the same `/v1/chat/completions` route and keeps
`finish_reason`, `reasoning_content`, and the full usage block. Schema bodies come from
graphrag's own `CommunityReportResponse.model_json_schema()`; the extraction prompt is the real
`groundly/prompts/extract_graph.txt`.

The delta from the prior harness is one function. Everything else — `COMMUNITY_INPUT`,
`SAMPLE_TEXT`, the grading rubric, the `##`-split record counting — is that review's code
unchanged.

```python
def post(base_url: str, model: str, prompt: str, *, no_think: bool, schema: dict | None):
    """One /v1/chat/completions call, keeping the whole raw response."""
    body: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    if schema is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "CommunityReportResponse", "schema": schema, "strict": True},
        }
    if no_think:
        # Measured on ollama 0.30.x: reasoning_effort="none" works (68 -> 2 completion
        # tokens on a trivial prompt); chat_template_kwargs {"enable_thinking": false} is
        # silently IGNORED. Sending only the one that works, to avoid a confound.
        # LM Studio is the reverse and would need chat_template_kwargs here.
        body["reasoning_effort"] = "none"

    start = time.monotonic()
    r = httpx.post(
        f"{base_url}/chat/completions",
        json=body,
        headers={"Authorization": "Bearer not-needed"},
        timeout=httpx.Timeout(10.0, read=900.0),
    )
    return {"raw": r.json(), "secs": round(time.monotonic() - start, 1)}
```

Per response it keeps `finish_reason`, `usage.completion_tokens`,
`usage.completion_tokens_details.reasoning_tokens` (null on Ollama), and the length of
`message.reasoning_content` — the last being the only reliable reasoning signal on this
runtime.

Verifying the reasoning switch before trusting any arm, which is worth doing per model since
its behaviour is not uniform:

```bash
curl -s localhost:11434/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"gemma4-12b-16k","messages":[{"role":"user","content":"What is 2+2?"}],
       "stream":false,"reasoning_effort":"none"}' | jq '.usage, .choices[0].message'
```

On `gemma4:12b` this drops completion tokens 68 → 2 with `reasoning_content` empty. On
`qwen3.5:4b` it empties `reasoning_content` while completion tokens stay in the thousands —
the relocation described in finding 2.
