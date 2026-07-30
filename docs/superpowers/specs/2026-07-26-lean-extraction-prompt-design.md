# A course-tuned entity-extraction prompt

## Context

graphrag's default extraction prompt is sent **once per chunk** and costs ~1620 tokens. On `apd` (1,194 chunks) that is 1.93M of the build's 2.12M input tokens: **91% of the bill is one fixed prompt re-sent 1,194 times**, because Groundly's chunks average 156 tokens against a 512 ceiling and the preamble dwarfs them.

The prompt is also aimed at the wrong target. Its entity types are `organization / person / geo / event` and its worked examples are news-wire scenarios (a stock market report, a political summit). For a parallel-and-distributed-algorithms course that produces a graph of the wrong *kind* — it looks for organisations and places in material that is about mutexes, consensus and complexity bounds.

So one change addresses both the dominant cost and a quality ceiling. That combination is why this is worth doing now rather than as a later optimisation: with paid Groq unavailable and a 100k-tokens/day free cap, build cost has stopped being a nice-to-have.

### Measured, not assumed

| prompt | tokens | sent |
|---|---|---|
| `extract_graph` | 1,620 | **per chunk** |
| `community_report` | 2,214 | per community (22 on `test_graph` — ~7% of the build) |
| `summarize_descriptions` | 183 | per entity description |

Inside the extraction prompt: **instructions are 379 tokens, worked examples are 1,236 (77%)**. The examples are the entire opportunity; `community_report` and `summarize_descriptions` are not worth touching.

> **Revised 2026-07-26 during implementation.** The original draft claimed a ~580-token preamble. That is not reachable: the fixed floor — instructions (379) + the `-Real Data-` tail (38) + section markers — is **417 tokens**, and graphrag's own smallest worked examples are 275 and 219 tokens, so 580 implies a ~163-token example, thinner than anything graphrag ships. Example thinness is precisely the format-anchoring risk this spec identifies below, so the prompt was budgeted at **≤700 tokens** and the acceptance criterion derived from that instead of the reverse. The figures below are the shipped ones, measured through the real `estimate_cost` path.

Replacing three news examples with one compact course example gives a **696-token** preamble:

| subject | chunks | chunk text | now | lean | saving |
|---|---|---|---|---|---|
| `test_graph` | 355 | 82,916 | 658,016 | **329,996** | 1.99× |
| `apd` | 1,194 | 186,495 | 2,120,775 | **1,017,519** | 2.08× |

**This does not unlock the Groq free tier.** `apd` at ~1.0M tokens is still ~10 days of a 100k/day cap. It roughly halves cost on any paid provider, roughly halves local wall-clock (time tracks tokens), and improves graph quality — it does not make a large corpus free.

### The quality problem, measured

The default-prompt build of `test_graph` (355 chunks of parallel-and-distributed-algorithms material) that was already on disk:

| | count |
|---|---|
| entities | 115 |
| relationships | 208 |
| communities | 23 |
| **types** | 75 `ORGANIZATION`, 34 `EVENT`, 4 `PERSON`, 1 `GEO`, 1 `NONE` |

Zero concepts, zero algorithms. This is the A/B baseline (snapshotted to `~/.groundly/.baselines/test_graph-default-prompt/`), and it is why criterion 3 below is a floor rather than a band.

## Design

### 1. A bundled prompt, overridable

`groundly/prompts/extract_graph.txt`, shipped as package data. `hatchling` with `packages = ["groundly"]` includes non-Python files under that tree, so no `pyproject.toml` change is needed — worth asserting in a test rather than trusting.

graphrag's `ExtractGraphConfig.prompt` takes a **filesystem path** and `resolved_prompts()` does `Path(self.prompt).read_text()`, so the file must exist as a real path for the duration of the build. Resolve it with `importlib.resources.as_file()` in a context manager spanning `build_index`, which is correct for both unzipped and zipped installs.

Content rules for the replacement:
- Keep graphrag's instruction block and its delimited output format **verbatim** (379 tokens). It defines the record format the downstream parser depends on exactly. A test asserts identity against `GRAPH_EXTRACTION_PROMPT` **modulo trailing whitespace**, so a graphrag upgrade that changes it fails loudly rather than diverging silently. *(Amended 2026-07-30: the assertion was byte-identity, and broke on `73023cd` — a rename sweep that stripped the five lone-space lines and added a final newline. Upstream carries trailing whitespace this repo strips everywhere else; it cannot reach the record format, so tolerating it costs no detection and stops the guard firing on repo hygiene.)*
- Replace the three news examples with **one** worked example drawn from course-like material (a short passage about mutual exclusion yielding concepts, an algorithm, and relationships). Write it as **original prose** — the file ships inside the wheel, so lifting text from actual course PDFs is a licensing problem.
- Keep `{entity_types}` and `{input_text}`.

> **Corrected during implementation.** The draft said to keep "the three delimiter placeholders — graphrag interpolates them". It does not. graphrag 3.1.0 formats the prompt with *only* `input_text` and `entity_types` (`graph_extractor._process_document`) and parses with hardcoded `TUPLE_DELIMITER`/`RECORD_DELIMITER`/`COMPLETION_DELIMITER` constants; the delimiters appear in the prompt as literal text. A prompt containing `{tuple_delimiter}` raises `KeyError` inside the per-chunk `except Exception` — **every chunk failing, silently**. So they are now *rejected* by prompt validation, not required. The preflight probe passed those three keys to `.format()`, which made it laxer than the build it exists to predict; it now formats exactly as graphrag does.

**Do not drop examples entirely.** That would reach ~379 tokens (4.3×), but few-shot examples are what hold the model to the delimited record format, and unparseable output fails *silently per chunk* — the exact class of failure this branch just spent a day hardening against. One example is the deliberate trade: 2.3× with the format anchored, rather than 4.3× betting on instruction-following.

### 2. Entity types

Default to `concept, algorithm, data_structure, theorem, technique, tool, metric, person`.

`person` stays — courses cite Dijkstra and Lamport and those are legitimate graph nodes. `organization`, `geo` and `event` go; they are news artifacts that produce noise on course material.

These lean CS-ward, which matches the pilot subjects (decision 11: Parallel & Distributed Algorithms plus an ML course). A law or history course would want different types, which is what the override is for.

### 3. Two optional config knobs

Added to the existing `[graph]` section:

```toml
[graph]
extraction_prompt = "/path/to/custom.txt"   # unset = the bundled course-tuned prompt
entity_types = "concept,algorithm,person"   # unset = the bundled defaults
```

Both optional, both defaulting to the bundled values, so zero-config behaviour is the new default rather than graphrag's. They exist for two real reasons, not speculation: a student outside CS needs different types, and **the thesis's evaluation needs to compare prompts on the gold set** — swapping the prompt *is* the experiment.

The prompt override is a path, so it must be validated at read time with a named failure (missing file, unreadable, missing a required placeholder) rather than surfacing as a graphrag internal error.

### 4. Extraction provenance and staleness — the load-bearing part

`manifest.graphrag` records `version`, `extraction_model`, `corpus_hash`. `graph_is_stale()` compares **only `corpus_hash`**, so today a student can change entity types or swap the prompt and the graph silently stays as it was. Every query then answers from a graph built under different framing, with nothing signalling it.

Add `extraction_fingerprint: str | None = None` to the `Graphrag` manifest block: `sha256` over the resolved prompt text plus the sorted entity types. Then:

- `graph_is_stale()` reports staleness when the recorded fingerprint differs from the current one, exactly as it does for `corpus_hash`. Changing the prompt or types triggers a rebuild on the next `groundly index`, with the existing confirmation gate and cost estimate.
- `build_graph` records the fingerprint alongside `corpus_hash`, in the same write, so a refused build records neither.
- **On import**, a fingerprint mismatch marks the graph stale rather than dropping it. An imported graph is internally consistent with its own corpus; it was just built under different framing. The next `index` rebuilds it under local settings. This differs deliberately from the `corpus_hash` mismatch at `cli/sharing.py:113`, which *drops* the graph because that indicates the bundle's graph does not match its own `store.db`.

Additive-with-default, so `format_version` stays 1 — the precedent is decision 15's `ocr` block and decision 18's settings tables.

> **Two implementation corrections.**
> 1. **`cli/sharing.py` needs no change.** Import already preserves a bundle's whole `graphrag` block, so a differing imported fingerprint makes `graph_is_stale` report staleness at the next `index` with zero new code. "Mark stale rather than drop" is the existing behaviour, not a change to make.
> 2. **`graph_is_stale` returns `str | None`, not `bool`.** There are now three causes, and the CLI quotes the reason to the student. Saying "the corpus changed" when what they changed was `graph.entity_types` is the same class of confident-but-wrong message the gates in this module exist to prevent.
>
> The fingerprint hashes the types **in the order given, not sorted** (the draft said sorted): graphrag interpolates the list in order, so a reorder genuinely changes the prompt the model sees. One less special case.
>
> **No migration was needed.** Every subject's `manifest.graphrag.corpus_hash` was still `null` — no graph had ever been successfully recorded — so `None` fingerprint ⇒ stale needs no grandfather clause.

### 5. What deliberately does not change

- `CHUNK_MAX_TOKENS`, `EMBEDDING_DIM`, the bge-m3 pin — interchange contract, re-index migrations, not tweaks.
- `community_report` and `summarize_descriptions` prompts — 7% and less of the spend.
- Chunk packing. Packing several chunks per graphrag document would cut calls further but breaks the `document_id == chunk_id` identity that citation resolution depends on. Separate spec.

## Files

**New:** `groundly/prompts/extract_graph.txt`, `tests/test_packaging.py`

No `groundly/prompts/__init__.py` — the resource is addressed as `files("groundly") / "prompts/extract_graph.txt"`, which needs no package, and the wheel test proves hatchling ships it.

**Modified:** `groundly/core/config.py` (`GraphSettings.extraction_prompt`, `entity_types`, and the writer), `groundly/core/manifest.py` (`Graphrag.extraction_fingerprint`), `groundly/llm/graphrag_adapter.py` (`resolve_extraction_prompt`, `extraction_entity_types`, `extraction_fingerprint`, `ExtractionPromptError`; the preamble is now measured per call rather than at import, since a module constant would price the wrong prompt), `groundly/ingestion/graph.py` (`_build_config` takes prompt/types; `build_graph` records the fingerprint in the same write as `corpus_hash`; `graph_is_stale` returns a reason; the probe's format keys), `groundly/cli/subjects.py` (quote the staleness reason; catch the named prompt error), `groundly/cli/models.py` (display the new settings)

**Not modified:** `groundly/cli/sharing.py` — see correction 1 above.

**Docs:** decision 22 in `docs/groundly-spec.md`; `docs/guides/graphrag-provider.md` (what the prompt targets, how to override, that changing it forces a rebuild); `docs/architecture/retrieval.md` if it characterises graph content

## Acceptance criteria

Cost and correctness are easy; **quality is the one that must not be assumed**.

Automated (pytest) — 1, 5, 6, 7, 8. Manual A/B on a real corpus — 2, 3, 4.

1. ✅ `estimate_cost` for `apd` drops from ~2.12M to **≤1.02M** tokens (measured: **1,017,519**), priced off the bundled prompt rather than graphrag's. *(Threshold revised from 950k — see the preamble-budget correction above.)*
2. A `test_graph` build completes: `entities.parquet`, `communities.parquet` and `community_reports.parquet` all non-empty, extraction failure rate at or below the existing gate.
3. **Entity count does not collapse** — a **floor**, not a band: **≥115 entities and ≥208 relationships**, the measured default-prompt baseline. *(Revised from "within 30%": that baseline is already a starved 0.32 entities/chunk with 95% wrong types, so a symmetric band would fail the improvement this change is for. The real risk is extracting less.)*
4. **Type distribution shifts as intended** — a majority of entities typed `concept`/`algorithm`/`data_structure`/`theorem`, and no entity typed `organization`/`geo`/`event` (those types no longer exist). Compare case-insensitively: graphrag uppercases types at parse time.
5. ✅ Changing `graph.entity_types` or `graph.extraction_prompt` makes `graph_is_stale()` report staleness, naming which of the two changed, and the next `index` offers a rebuild.
6. ✅ A custom `extraction_prompt` that is missing, unreadable, lacks a required placeholder, **or contains a delimiter placeholder** fails with a named cause before any LLM call — and without destroying an existing graph.
7. ✅ The bundled prompt is present in an installed wheel (`@slow`, builds a real wheel).
8. ✅ The preflight probe exercises the bundled prompt, formatted with exactly the keys graphrag formats it with.

A ninth, added during implementation: the bundled prompt is capped at **700 est. tokens** by test. This preamble's size *is* the build's bill, so an example that grows silently re-inflates every build.

## Verification

```bash
.venv/bin/python -m pytest -q && .venv/bin/ruff check groundly tests && .venv/bin/ruff format --check groundly tests
```

The comparison that matters is A/B on one corpus. `test_graph` is the right size (355 chunks, ~330k tokens lean).

> **The cache does not help here.** `graphrag_llm/cache/create_cache_key.py` hashes the rendered `messages`, so a changed prompt is a **100% cache miss** — re-running the default arm would cost a fresh 658k tokens. It isn't necessary: that arm's parquet was already on disk and is snapshotted to `~/.groundly/.baselines/test_graph-default-prompt/`, with its counts recorded above. One caveat — that build produced no `community_reports.parquet` (a DeepSeek JSON-mode failure), so **report counts are not comparable across arms**; entity and relationship counts are.

```bash
groundly index test_graph --graph --debug
```

Capture for each arm — default prompt vs bundled lean prompt — the estimated tokens at the confirmation gate, entity/relationship/community counts from the parquet, and the type distribution. Then check the graph is actually *better*, not just cheaper:

```bash
groundly ask test_graph "how do the course's main topics relate?" --debug
```

Expect a `graph-global` or `hybrid-local` arm in the debug line, and citations resolving to real pages.

Packaging check, since a missing prompt file would only fail once installed:

```bash
uv tool install --reinstall --from . groundly && groundly index test_graph --graph
```

## Risks

- **Extraction quality regresses on a cheaper prompt.** Criterion 3 is the guard; if entity count collapses, add a second example and re-measure rather than shipping the saving.
- **One example may not anchor the format for weaker local models.** graphrag swallows unparseable output per chunk, so this shows up as a rising failure count — visible now via `GraphBuildResult.failed`, which is exactly what that counter is for. Watch it on the LM Studio arm specifically.
- **CS-leaning defaults.** Honest limitation for a general-purpose tool; the override knob and the thesis text should both say so plainly.

## Out of scope

Chunk packing (separate spec, breaks citation identity); the 5% extraction-failure threshold, which a real run has already exceeded at 5.82% and should be set from evidence; parquet import validation (documented in `security.md` residual risks); provider selection.
