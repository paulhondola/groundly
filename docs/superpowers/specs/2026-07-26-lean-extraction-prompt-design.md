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

Inside the extraction prompt: **instructions are 379 tokens, worked examples are 1,241 (77%)**. The examples are the entire opportunity; `community_report` and `summarize_descriptions` are not worth touching.

Replacing three news examples with one compact course example gives a ~580-token preamble:

| subject | chunks | chunk text | now | lean | saving |
|---|---|---|---|---|---|
| `test_graph` | 355 | 82,916 | 658,016 | **288,816** | 2.28× |
| `apd` | 1,194 | 186,495 | 2,120,775 | **879,015** | 2.41× |

**This does not unlock the Groq free tier.** `apd` at 879k tokens is still ~9 days of a 100k/day cap. It roughly halves cost on any paid provider, roughly halves local wall-clock (time tracks tokens), and improves graph quality — it does not make a large corpus free.

## Design

### 1. A bundled prompt, overridable

`groundly/prompts/extract_graph.txt`, shipped as package data. `hatchling` with `packages = ["groundly"]` includes non-Python files under that tree, so no `pyproject.toml` change is needed — worth asserting in a test rather than trusting.

graphrag's `ExtractGraphConfig.prompt` takes a **filesystem path** and `resolved_prompts()` does `Path(self.prompt).read_text()`, so the file must exist as a real path for the duration of the build. Resolve it with `importlib.resources.as_file()` in a context manager spanning `build_index`, which is correct for both unzipped and zipped installs.

Content rules for the replacement:
- Keep graphrag's instruction block and its delimited output format **verbatim** (379 tokens). It defines `tuple_delimiter` / `record_delimiter` / `completion_delimiter` handling, and the downstream parser depends on it exactly.
- Replace the three news examples with **one** worked example drawn from course-like material (a short passage about mutual exclusion yielding a concept, an algorithm, a relationship).
- Keep `{entity_types}`, `{input_text}` and the three delimiter placeholders — graphrag interpolates them, and our probe formats the same way.

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

- `graph_is_stale()` returns True when the recorded fingerprint differs from the current one, exactly as it does for `corpus_hash`. Changing the prompt or types triggers a rebuild on the next `groundly index`, with the existing confirmation gate and cost estimate.
- `build_graph` records the fingerprint alongside `corpus_hash`, in the same write, so a refused build records neither.
- **On import**, a fingerprint mismatch marks the graph stale rather than dropping it. An imported graph is internally consistent with its own corpus; it was just built under different framing. The next `index` rebuilds it under local settings. This differs deliberately from the `corpus_hash` mismatch at `cli/sharing.py:113`, which *drops* the graph because that indicates the bundle's graph does not match its own `store.db`.

Additive-with-default, so `format_version` stays 1 — the precedent is decision 15's `ocr` block and decision 18's settings tables.

### 5. What deliberately does not change

- `CHUNK_MAX_TOKENS`, `EMBEDDING_DIM`, the bge-m3 pin — interchange contract, re-index migrations, not tweaks.
- `community_report` and `summarize_descriptions` prompts — 7% and less of the spend.
- Chunk packing. Packing several chunks per graphrag document would cut calls further but breaks the `document_id == chunk_id` identity that citation resolution depends on. Separate spec.

## Files

**New:** `groundly/prompts/extract_graph.txt`, `groundly/prompts/__init__.py`

**Modified:** `groundly/core/config.py` (`GraphSettings.extraction_prompt`, `entity_types`), `groundly/core/manifest.py` (`Graphrag.extraction_fingerprint`), `groundly/llm/graphrag_adapter.py` (resolve prompt + types; `_EXTRACTION_PREAMBLE_TOKENS` must measure the *bundled* prompt so `estimate_cost` stays honest), `groundly/ingestion/graph.py` (`_build_config` passes prompt/types; `build_graph` records the fingerprint; `graph_is_stale` compares it), `groundly/cli/sharing.py` (import: fingerprint mismatch → stale, not dropped), `groundly/cli/models.py` (display the new settings)

**Docs:** decision 22 in `docs/groundly-spec.md`; `docs/guides/graphrag-provider.md` (what the prompt targets, how to override, that changing it forces a rebuild); `docs/architecture/retrieval.md` if it characterises graph content

## Acceptance criteria

Cost and correctness are easy; **quality is the one that must not be assumed**.

1. `estimate_cost` for `apd` drops from ~2.12M to **≤950k** tokens, and its preamble constant reflects the bundled prompt.
2. A `test_graph` build completes: `entities.parquet`, `communities.parquet` and `community_reports.parquet` all non-empty, extraction failure rate at or below the existing gate.
3. **Entity count does not collapse** — within 30% of a default-prompt build on the same corpus. A cheaper prompt that extracts half as much is a regression, not a saving.
4. **Type distribution shifts as intended** — a majority of entities typed `concept`/`algorithm`/`data_structure`/`theorem`, and no entity typed `organization`/`geo`/`event` (those types no longer exist).
5. Changing `graph.entity_types` makes `graph_is_stale()` return True and the next `index` offers a rebuild.
6. A custom `extraction_prompt` path that is missing, or lacks a required placeholder, fails with a named cause before any LLM call.
7. The bundled prompt is present in an installed wheel (guards the packaging assumption).
8. The preflight probe exercises the bundled prompt — already true via `resolved_prompts()`, so this is a regression test, not new code.

## Verification

```bash
.venv/bin/python -m pytest -q && .venv/bin/ruff check groundly tests && .venv/bin/ruff format --check groundly tests
```

The comparison that matters is A/B on one corpus. `test_graph` is the right size (355 chunks, ~289k tokens lean), and `cache/` survives rebuilds so the second build only re-runs what changed:

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
