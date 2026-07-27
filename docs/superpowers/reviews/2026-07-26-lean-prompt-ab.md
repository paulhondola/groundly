# A/B: default vs course-tuned extraction prompt

Decision 22. Criteria 1 and 5–8 are covered by pytest. Criteria **2, 3 and 4 need a real
corpus and a real provider** and are recorded here.

## Arm A — graphrag's default prompt (already measured)

From the build left in `~/.groundly/test_graph/graph/` (355 chunks, parallel-and-distributed
algorithms), snapshotted to `~/.groundly/.baselines/test_graph-default-prompt/`.

| metric | value |
|---|---|
| preamble | 1,620 tokens/chunk |
| estimated build | 658,016 tokens |
| entities | **115** |
| relationships | **208** |
| communities | 23 |
| community reports | **0** — DeepSeek rejected JSON mode; not comparable across arms |
| type distribution | 75 `ORGANIZATION`, 34 `EVENT`, 4 `PERSON`, 1 `GEO`, 1 `NONE` |

Zero concepts, zero algorithms.

**Why this arm is not re-run:** `graphrag_llm/cache/create_cache_key.py` hashes the rendered
`messages`, so a prompt change is a 100% cache miss. Rebuilding arm A would cost a fresh
658k tokens for numbers already on disk.

## Arm B — the bundled course-tuned prompt

Estimates verified through the real `estimate_cost` path:

| metric | value |
|---|---|
| preamble | **696** tokens/chunk |
| estimated build | **329,996** tokens (1.99× cheaper) |

To run:

```bash
groundly index test_graph --graph --debug
```

Then capture the counts:

```bash
python -c "
import pandas as pd, pathlib
g = pathlib.Path.home() / '.groundly/test_graph/graph'
for f in ('entities', 'relationships', 'communities', 'community_reports'):
    p = g / f'{f}.parquet'
    print(f, len(pd.read_parquet(p)) if p.exists() else 'MISSING')
print(pd.read_parquet(g / 'entities.parquet')['type'].str.upper().value_counts().to_string())
"
```

| metric | value |
|---|---|
| entities | _pending_ |
| relationships | _pending_ |
| communities | _pending_ |
| community reports | _pending_ |
| type distribution | _pending_ |
| chunks failed extraction | _pending_ |

## Pass conditions

- **Criterion 2** — `entities.parquet`, `communities.parquet` and `community_reports.parquet`
  all non-empty; extraction failure rate at or below the 5% gate.
- **Criterion 3 (floor, not a band)** — **entities ≥ 115 and relationships ≥ 208**. A floor
  because arm A is already a starved 0.32 entities/chunk with 95% wrong types: a symmetric
  ±30% band would fail the improvement this change exists to produce. The real risk is a
  cheaper prompt extracting *less*.
- **Criterion 4** — a majority typed `CONCEPT`/`ALGORITHM`/`DATA_STRUCTURE`/`THEOREM`, and
  zero `ORGANIZATION`/`GEO`/`EVENT`. Compare uppercased: graphrag uppercases at parse time.

Then confirm the graph is better, not just cheaper:

```bash
groundly ask test_graph "how do the course's main topics relate?" --debug
```

Expect a `graph-global` or `hybrid-local` arm and citations resolving to real pages.

## If it fails

- **Entities below the floor** — add a second worked example and re-measure. Do not ship the
  saving. The budget test caps the prompt at 700 tokens; a second example needs that cap
  raised deliberately, with the cost table in decision 22 updated to match.
- **Failure rate above 5%** — note that a real run already exceeded this gate at 5.82%
  *before* this change. The threshold is deliberately out of scope here precisely so a
  rising rate stays attributable; check arm B's rate against that 5.82%, not against 0.
- **Format failures on a weak local model** — the one-example trade is the known risk. It
  surfaces as a rising `GraphBuildResult.failed`. Watch the LM Studio arm specifically.
