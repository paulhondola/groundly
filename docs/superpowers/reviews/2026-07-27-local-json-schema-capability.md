# What local models are actually capable of, structured-output wise

Measured 2026-07-27 on **M1 Pro, 16 GB unified memory, LM Studio** (server on
`127.0.0.1:1234`). Reproduction harness in the appendix.

## Why this was measured

`docs/guides/graphrag-provider.md` rested its local-model story on a single data point —
one table row saying `qwen/qwen3.5-9b` supports `json_schema` — and on two claims that had
never been measured: that structured-output support is "a property of the **model**, not the
provider", and that extraction "needs a mid-tier cloud model, never a small local model".

The headline result is that the one measured row **was wrong**, and wrong in the most
expensive possible way: the model it names passes Groundly's preflight probe and then
produces an empty community report on every single call.

## Summary

| # | Finding | Severity |
| --- | --- | --- |
| 1 | `qwen/qwen3.5-9b` returns **HTTP 200 with `content: ""`** for every structured-output call. The complete, correct JSON is stranded in `reasoning_content`. | **critical** |
| 2 | The preflight probe cannot detect this — it checks only that no exception was raised, never that a response body exists. | **critical** |
| 3 | Reasoning tokens are billed against the context window, so a reasoning model at 4096 spends the entire budget thinking and emits nothing. | high |
| 4 | `lms load -c` is honored by the GGUF/llama.cpp engine and **ignored by the MLX engine**. | medium |
| 5 | LM Studio no longer defaults to 4096 context, as the guide states — it defaulted to 32768. | low |
| 6 | Where structured output does work, fidelity is good; **latency** is what rules out local extraction (~22 h optimistic for the guide's reference corpus). | high |
| 7 | `openai/gpt-oss-20b` cannot load on 16 GB at all. | informational |

## The four tiers

"Capable of JSON-schema output" collapses four separable questions. Groundly's preflight
probe ([graph.py:252](../../../groundly/ingestion/graph.py)) answers only the first.

| Tier | Question | Decided by |
| --- | --- | --- |
| **T0 Accept** | Does the endpoint return 200 for graphrag's `response_format`? | the runtime's grammar converter |
| **T1 Conform** | Does the returned text validate against `CommunityReportResponse`? | runtime + chat template |
| **T2 Fidelity** | Is the report usable, or schema-shaped filler? | the model |
| **T3 Throughput** | Can it finish a build this decade? | model + hardware |

The schema under test is graphrag's own, and is deliberately not flat — nested `$defs`/`$ref`
plus an array of objects, with litellm adding `strict: true`:

```
CommunityReportResponse { title, summary, findings: FindingModel[], rating, rating_explanation }
  $defs: FindingModel { summary, explanation }
```

## Results

`ctx` is the **loaded** context length read back from LM Studio's `/api/v0/models`, not the
value requested (see finding 4). Reports are n=3 against a fixed community input.

| model | engine | ctx | T0 schema | T0 `json_object` | T1 conform | T2 fidelity | T3 extraction call |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `google/gemma-4-12b-qat` | gguf | 4096 | pass | refused | **0/3** | n/a | 0 entities (truncated) |
| `google/gemma-4-12b-qat` | gguf | 16384 | pass | refused | **3/3** | good, unstable rating | 12 ent / 11 rel / 0 malformed, 199.9 s |
| `qwen/qwen3.5-9b` | mlx | 32768 | "pass" | refused | **0/3** | n/a — no content at all | 10 ent / 11 rel / 0 malformed, 311.5 s |
| `google/gemma-4-12b` | mlx | 19456 | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ |
| `openai/gpt-oss-20b` | mlx | — | **cannot load** | — | — | — | — |

Both engines refuse the older JSON-mode capability identically, reproducing the string the
guide already quotes — which is the harness's own correctness check:

```
HTTP 400: {'error': "'response_format.type' must be 'json_schema' or 'text'"}
```

### Finding 1 — qwen3.5-9b emits the right answer into the wrong channel

Every structured-output call to `qwen/qwen3.5-9b` returned `200 OK` with an empty
`message.content`. Direct capture of the raw response on the real community-report prompt:

```
finish_reason: "stop"
usage: { prompt_tokens: 2370, completion_tokens: 544, total_tokens: 2914,
         completion_tokens_details: { reasoning_tokens: 543 } }
content len:   0
reasoning len: 2763
```

`finish_reason` is `stop`, not `length`, and only 2914 of 32768 tokens were used — this is
**not** truncation. 543 of 544 completion tokens were classified as reasoning, and the tail of
`reasoning_content` is the finished report:

```
…"explanation": "Robert Blumofe is a person who contributed to the theoretical foundations
of work stealing. He proved the expected time bound for randomized work stealing…
[Data: Entities (7), Relationships (5)]"}], "rating": 2.5, "rating_explanation": "…"}
```

The model did the work correctly, grounded it in the supplied records, and produced
well-formed JSON. The MLX engine routed all of it into the reasoning channel, so
`message.content` — the field `llm/chat.py` reads, correctly — is the empty string.

The mechanism: **the schema grammar constrains the content channel only.** A model that never
exits its thinking channel is never constrained by the grammar and never fills content. What
lands in `reasoning_content` is well-formed here by luck — graphrag's prompt describes the
JSON shape in prose, so the model imitated it — not by enforcement.

Reproduced 5/5 (three harness reports, one raw capture, plus the T0 probe itself).

### Finding 2 — the preflight probe passes a model that produces nothing

`_probe_call` ([graph.py:272](../../../groundly/ingestion/graph.py)) wraps the call in
`try/except` and records a success trace on any non-throwing return. It never inspects
`result.text`. `complete()` returns `ChatResult(text=response.choices[0].message.content)`,
which for qwen3.5-9b is `""`. No exception is raised, so the probe passes.

The T0 row for qwen in the table above is quoted as `"pass"` for exactly this reason: the probe
call succeeded and returned **zero characters**.

The consequence is precisely the failure the probe exists to prevent, documented in its own
docstring at [graph.py:240](../../../groundly/ingestion/graph.py): every community report call
comes back empty, graphrag's `on_error` swallows each one, the reports table ends up empty, and
the build dies merging that empty frame on `KeyError: 'community'` — after the whole extraction
pass has been paid for. On this hardware that pass is roughly a day of compute.

**No probe change is made here.** Per the plan, that is a separate decision. The cheap fix is
one line — treat empty `text` as a probe failure — but it interacts with `reasoning_content`
handling and with the shape of the error message, and belongs in its own change set.

### Finding 3 — reasoning tokens are charged to the context window

`google/gemma-4-12b-qat` at 4096 (GGUF, the value the guide names as LM Studio's default)
failed all three reports. Raw capture:

```
prompt_tokens: 2356  completion_tokens: 1740  total_tokens: 4096
completion_tokens_details: { reasoning_tokens: 1737 }
finish_reason: "length"
content: ""
```

The 2356-token prompt plus 1737 reasoning tokens exactly exhausts 4096. Three tokens reached
the schema-constrained answer. Same endpoint, same schema, same model, `content: ""` — but
unlike finding 1 this **is** truncation, and it is fixed by raising the context: at 16384 the
same model conformed 3/3.

The extraction stage fails the same way at 4096 — **0 entities, 0 relationships** — which is
the silent-empty-graph scenario the guide already warns about, now with a measured cause.

Note the probe cannot catch this either: its probe prompt is 24 tokens and leaves 4000+ for
reasoning, so it passes comfortably on a model that cannot do the real work.

### Finding 4 — `-c` is honored on GGUF, ignored on MLX

Requested vs. loaded context, read back from `/api/v0/models`:

| model | engine | requested | loaded |
| --- | --- | --- | --- |
| `google/gemma-4-12b-qat` | gguf | 4096 | 4096 |
| `google/gemma-4-12b-qat` | gguf | 16384 | 16384 |
| `qwen/qwen3.5-9b` | mlx | 4096 | 32768 |
| `qwen/qwen3.5-9b` | mlx | 16384 | 32768 |
| `google/gemma-4-12b` | mlx | 16384 | 19456 |

`--parallel` was honored on both engines; only `-c` differs. There were no saved per-model
overrides (`~/.lmstudio/.internal/user-concrete-model-default-config/` was empty).

This matters for the guide's advice to "set `graph.context_window` to whatever your model is
actually loaded with": on MLX you cannot choose that number, you can only read it back.

### Finding 5 — the 4096 default is stale

The guide states "A local model loaded at 4096 (LM Studio's common default)". On this install
`qwen/qwen3.5-9b` loaded at **32768** with no context flag at all, and LM Studio's default
`--parallel` is **4**, not 1. The 4096 failure in finding 3 had to be produced deliberately.

### Finding 6 — where it works, quality is fine and speed is not

`gemma-4-12b-qat` @ 16384 (the only configuration that produced usable structured output):

| | rep 0 | rep 1 | rep 2 |
| --- | --- | --- | --- |
| conforms | yes | yes | yes |
| findings | 6 | 5 | 5 |
| input entities covered | 7/7 | 7/7 | 7/7 |
| hallucinated entities | 0 | 0 | 0 |
| rating | 4.0 | 4.0 | **8.0** |
| seconds | 159.4 | 96.9 | 242.9 |

Graded against the rubric fixed before any output was seen. Coverage and grounding are
clean — every capitalized term not present in the input was an ordinary sentence-initial
English word, with no fabricated entities in any of the three. The weak spot is **rating
calibration**: identical input scored 4.0, 4.0, and 8.0, and the `rating_explanation` text is
near-identical across all three despite the doubled score. Since `rating` is what global search
ranks communities by, that variance degrades `overview` ordering even when the prose is good.

Latency is the disqualifier. One real extraction call took **199.9 s**, and the build makes one
per chunk:

| corpus | serial | at `--parallel 4`, optimistic 3× |
| --- | --- | --- |
| 355 chunks | 19.7 h | 6.6 h |
| 1194 chunks (guide's reference corpus) | 66.3 h | **22.1 h** |

Extraction pass only — community reports and description summaries are billed on top, and on
this hardware each report took 97–243 s. `qwen/qwen3.5-9b` was slower still at 311.5 s per
extraction call (103 h serial), even though its extraction *output* was clean.

**So the guide's conclusion survives, but its stated reason does not.** A small local model
does not produce a garbled graph — gemma's extraction output was 12 entities, 11
relationships, 0 malformed records, and its reports were accurate. It produces a *correct*
graph roughly a day later. The argument against local extraction is throughput, not quality.

### Finding 7 — gpt-oss-20b does not fit in 16 GB

```
Estimated GPU Memory: 15.78 GiB   (Confidence: LOW)
Error: Model loading was stopped due to insufficient system resources.
```

Worth recording because `gpt-oss-120b` is named in the guide as schema-capable on Groq: the
locally-runnable sibling is not an option on a 16 GB machine. `lms load --estimate-only`
answers this before a 12 GB download, which is the useful takeaway.

## What this means for the docs

1. The table's `qwen/qwen3.5-9b` / `json_schema` = yes row is **wrong** and must be corrected —
   acceptance is not capability.
2. "Support is a property of the model, not the provider" holds across cloud endpoints and is
   misleading for local runtimes, where the constrained decoder makes T0 nearly uniform and the
   binding constraint moves to T1 (channel routing) and T3 (latency).
3. "Never a small local model" is right for the wrong reason — replace the quality argument
   with the measured latency.
4. The 4096-default claim is stale, and the `graph.context_window` advice needs an MLX caveat.

## Scope and limits

- One machine, one LM Studio install, one build each of four models. Engine behaviour may
  differ across LM Studio versions.
- **Ollama was not measured** (not installed). The guide's claims about Ollama remain
  unverified — including the `-c` equivalent and the reasoning-channel behaviour, both of which
  differ per runtime.
- No end-to-end `groundly index --graph` run; the latency figures are extrapolated from single
  calls and ignore graphrag's concurrency and its response cache.
- `~/.groundly/` was never written to — the harness points `GROUNDLY_HOME` at a scratch
  directory. No subject was indexed.

## Appendix — reproduction harness

Run `lms server start`, then per model: `lms unload --all`, `lms load <key> -c <n> --parallel 1 -y`,
read the loaded context back from `/api/v0/models`, and run the script below with that number.

Everything goes through `groundly.llm.chat.complete()` with graphrag's own
`CommunityReportResponse`, so the wire request is what a real build sends — the probe docstring
at [graph.py:194](../../../groundly/ingestion/graph.py) explains why any approximation here has
historically been wrong in both directions.

```python
"""Measure local-model JSON-schema capability for Groundly's graph build.

Usage: probe_schema.py <model-id> <loaded-context> [outfile.json]
"""

import json
import os
import pathlib
import sys
import time

SCRATCH = pathlib.Path(__file__).resolve().parent
HOME = SCRATCH / "groundly-home"
HOME.mkdir(exist_ok=True)
os.environ["GROUNDLY_HOME"] = str(HOME)   # never touch ~/.groundly

REPO = pathlib.Path("/Users/paulhondola/Developer/groundly")
sys.path.insert(0, str(REPO))

CONFIG = """
[providers.extraction]
base_url = "http://localhost:1234/v1"
model = "{model}"
api_key = ""

[llm]
timeout_seconds = 900
"""

# Fixed community input, held constant across models so T2 is comparable. Entity names are
# distinctive: anything outside this list appearing in a report is a hallucination.
COMMUNITY_INPUT = """
-----Entities-----
id,entity,type,description
1,WORK STEALING,algorithm,Idle threads steal tasks from the deques of busy threads
2,CILK,tool,Task-parallel language runtime built on work stealing
3,DEQUE,data_structure,Double-ended queue holding a worker's ready tasks
4,BRENT'S THEOREM,theorem,Bounds parallel runtime by work and span
5,WORK,metric,Total operations executed by a computation
6,SPAN,metric,Length of the longest dependency chain
7,ROBERT BLUMOFE,person,Proved the expected time bound for randomized work stealing

-----Relationships-----
id,source,target,description,weight
1,CILK,WORK STEALING,Cilk's scheduler is a randomized work-stealing scheduler,9
2,WORK STEALING,DEQUE,Each worker owns a deque it pushes and pops locally while thieves steal from the other end,9
3,BRENT'S THEOREM,WORK,Brent's bound is expressed in terms of the work term,8
4,BRENT'S THEOREM,SPAN,Brent's bound is expressed in terms of the span term,8
5,ROBERT BLUMOFE,WORK STEALING,Blumofe and Leiserson proved the expected running time of randomized work stealing,9
6,WORK,SPAN,Work and span together determine available parallelism,7
"""

SAMPLE_TEXT = (
    "A randomized work-stealing scheduler assigns each worker a double-ended queue of "
    "ready tasks. A worker pushes and pops from the bottom of its own deque, so the "
    "common case needs no synchronization; when a worker runs out of work it becomes a "
    "thief and steals from the top of a victim deque chosen uniformly at random. "
    "Blumofe and Leiserson proved that this scheme finishes a computation with work T1 "
    "and span Tinf in expected time T1/P + O(Tinf) on P processors, which matches "
    "Brent's theorem up to a constant factor."
)


def set_model(model: str) -> None:
    (HOME / "config.toml").write_text(CONFIG.format(model=model))


def timed(fn):
    start = time.monotonic()
    try:
        return fn(), None, time.monotonic() - start
    except Exception as exc:  # noqa: BLE001 - the refusal string IS the measurement
        return None, f"{type(exc).__name__}: {exc}", time.monotonic() - start


def main() -> None:
    model, ctx = sys.argv[1], int(sys.argv[2])
    outfile = pathlib.Path(sys.argv[3]) if len(sys.argv) > 3 else SCRATCH / "results.json"
    set_model(model)

    from graphrag.index.operations.summarize_communities.community_reports_extractor import (
        CommunityReportResponse,
    )
    from graphrag.prompts.index.community_report import COMMUNITY_REPORT_PROMPT

    from groundly.llm.chat import complete

    report_prompt = COMMUNITY_REPORT_PROMPT.format(
        input_text=COMMUNITY_INPUT, max_report_length=str(min(1500, ctx // 8))
    )
    extraction_prompt = (
        (REPO / "groundly/prompts/extract_graph.txt")
        .read_text()
        .format(
            entity_types="concept,algorithm,data_structure,theorem,technique,tool,metric,person",
            input_text=SAMPLE_TEXT,
        )
    )

    rec: dict = {"model": model, "context": ctx}

    # T0: does the endpoint accept graphrag's nested $defs/$ref strict schema?
    res, err, secs = timed(
        lambda: complete(
            "extraction",
            [{"role": "user", "content": "Summarise a one-entity community."}],
            response_format=CommunityReportResponse,
        )
    )
    rec["t0_json_schema"] = {"ok": err is None, "error": err, "secs": round(secs, 1)}
    if res:
        rec["t0_json_schema"]["text"] = res.text   # NOTE: may be "" while ok is True

    # T0b: the older, weaker `json_object` capability.
    res, err, secs = timed(
        lambda: complete(
            "extraction",
            [{"role": "user", "content": "Return a JSON object with one key."}],
            response_format={"type": "json_object"},
        )
    )
    rec["t0_json_object"] = {"ok": err is None, "error": err, "secs": round(secs, 1)}

    # T1 + T2: three real community reports against the fixed input.
    rec["reports"] = []
    for i in range(3):
        res, err, secs = timed(
            lambda: complete(
                "extraction",
                [{"role": "user", "content": report_prompt}],
                response_format=CommunityReportResponse,
            )
        )
        sample: dict = {"i": i, "secs": round(secs, 1), "error": err}
        if res:
            sample["tokens"] = res.tokens
            sample["raw"] = res.text
            try:
                parsed = CommunityReportResponse.model_validate_json(res.text)
                sample["conforms"] = True
                sample["parsed"] = parsed.model_dump()
            except Exception as exc:  # noqa: BLE001
                sample["conforms"] = False
                sample["parse_error"] = f"{type(exc).__name__}: {exc}"
        rec["reports"].append(sample)

    # T3 + secondary: one real extraction call. Wall clock here is the unit the build
    # repeats once per chunk, and the output is what decides graph quality.
    res, err, secs = timed(
        lambda: complete("extraction", [{"role": "user", "content": extraction_prompt}])
    )
    ext: dict = {"secs": round(secs, 1), "error": err}
    if res:
        text = res.text or ""
        ext["tokens"] = res.tokens
        ext["prompt_chars"] = len(extraction_prompt)
        records = [r.strip() for r in text.split("##") if r.strip()]
        ext["entities"] = sum(1 for r in records if r.startswith('("entity"'))
        ext["relationships"] = sum(1 for r in records if r.startswith('("relationship"'))
        ext["malformed"] = sum(
            1 for r in records if not r.startswith(('("entity"', '("relationship"'))
        )
        ext["raw"] = text
    rec["extraction"] = ext

    prev = json.loads(outfile.read_text()) if outfile.exists() else []
    prev.append(rec)
    outfile.write_text(json.dumps(prev, indent=1))


if __name__ == "__main__":
    main()
```

The harness records `content` only. Findings 1 and 3 needed the raw response — `finish_reason`,
`completion_tokens_details.reasoning_tokens`, and `reasoning_content` — which is why those were
captured with a direct POST to `/v1/chat/completions` carrying the same
`CommunityReportResponse.model_json_schema()` body.
