# Thesis tables

Measured results, one finding per file, written by the `research-specialist` agent.

Each `tab-<slug>.tex` is a self-contained `\input{}` fragment: a single `table` float with
a `\caption` and `\label{tab:<slug>}`, and **no preamble**. The thesis document owns the
preamble and must load `booktabs` (plus `siunitx` if a table uses `S` columns).

```latex
\input{docs/thesis/tab-graph-build-cost}
```

## Two families, and why they must not merge

The tables fall into two groups that answer different kinds of question:

- **Controlled experiment, apd only** — `tab-graph-build-time`, `tab-graph-build-cost`,
  `tab-graph-shape`, `tab-graph-composition`, `tab-graph-extraction-procedure`,
  `tab-graph-two-factor`, `tab-graph-global-constant`, `tab-retrieval-quality-by-graph`,
  `tab-retrieval-mcnemar`, `tab-retrieval-leakage`. One corpus, three graph builds,
  everything else held constant. Their columns are **build conditions**.
- **Cross-subject comparison** — everything suffixed `-two-subjects`. Two corpora at one
  fixed build procedure. Their columns are **subjects**.

A second subject is not a fourth build condition. Adding a `passc` column to a table in the
first group would read as one and would destroy what makes that group controlled; a new
build of apd added to the second group would read as a third subject. Keep them apart.
Where a cross-subject table repeats an apd figure (it is the shipped `gptoss@0` build in
every case), it says so in its footnote and the two agree exactly.

## The comment header is the provenance

Every file opens with a comment block naming the measurement date, the subject and its
size, the model, the config fields that change the number, and the artifact the number
was read from. LaTeX drops comments at render time, so the thesis stays clean while the
file keeps its own audit trail.

`% Supersedes:` records what a file replaced. Tables are **overwritten in place** when a
better measurement of the same quantity exists — a real system improvement, or a
methodology fix that invalidates the old one — so the rendered thesis always shows the
current best while the file retains what came before.

Two things that are never reasons to overwrite: a rerun that merely came out luckier, and
a number that is more flattering. A worse result from a sound measurement still replaces
its predecessor; that is a finding.

## What must never appear here

`progress.db` is the privacy boundary (`.claude/rules/grounding-and-privacy.md`). Aggregate
from it freely — counts, sums, medians, percentiles — but no query strings, answers, notes
or quiz results reach a `.tex` file.
