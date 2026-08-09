# Thesis tables

Measured results, one finding per file, written by the `research-specialist` agent.

**Format: Markdown (`tab-<slug>.md`).** These are working artifacts — they get reformatted
into the thesis later, so they optimize for being read directly. Each file is a `#` title,
one pipe table, a sentence or two saying what it shows, and a provenance block.

The seventeen `.tex` files are the earlier LaTeX generation, kept as-is: they are
`\input{}`-ready fragments with `booktabs` rules and carry the same numbers. Nothing new
is written in that format; when a `.md` table supersedes one, the `.tex` file is the
historical version, not a second source of truth.

## The provenance block is the point

Every file ends with:

```markdown
> **Provenance**
> - Measured 2026-08-09 · apd, 187 materials / 1,193 chunks
> - Model: `openai/gpt-oss-120b` (DeepInfra)
> - Config: `graph.context_window=16384`, `graph.gleanings=0`, `concurrent_requests=25`
> - Source: `~/.groundly/apd/graph/stats.json`
> - Supersedes: 2026-08-03 `gemma-4-12b-qat` build — 15.02 h, unpriced (local)
```

It names the measurement date, the subject and its size, the model, the config fields that
change the number, and the artifact the number was read from. A blockquote so it reads as
apparatus rather than content — but visible, because these files are read directly and
hiding the sourcing defeats the purpose.

`Supersedes:` records what a file replaced. Tables are **overwritten in place** when a
better measurement of the same quantity exists — a real system improvement, or a
methodology fix that invalidates the old one — so the file always shows the current best
while retaining what came before.

Two things that are never reasons to overwrite: a rerun that merely came out luckier, and
a number that is more flattering. A worse result from a sound measurement still replaces
its predecessor; that is a finding.

## Two families of table — do not merge them

**The apd controlled series** (`tab-graph-build-*`, `tab-graph-shape`,
`tab-graph-composition`, `tab-graph-extraction-procedure`, `tab-graph-two-factor`,
`tab-retrieval-quality-by-graph`, `tab-retrieval-mcnemar`, `tab-retrieval-leakage`,
`tab-graph-global-constant`) describes **one corpus built three ways** — `gemma-4-12b-qat`
at `gleanings=0`, `gpt-oss-120b` at 0, and `gpt-oss-120b` at 1 — with corpus, chunking,
prompt and entity types held identical. That control is what lets the model effect be
separated from the gleaning effect.

**The cross-subject tables** (`tab-*-two-subjects`) compare apd against passc, both at the
shipped configuration.

A second *subject* is not a fourth *build condition*. Appending passc as a column to the
controlled series would render as a fourth experimental cell and destroy exactly what makes
that series worth anything.

## What must never appear here

`progress.db` is the privacy boundary (`.claude/rules/grounding-and-privacy.md`). Aggregate
from it freely — counts, sums, medians, percentiles — but no query strings, answers, notes
or quiz results reach a table file.
