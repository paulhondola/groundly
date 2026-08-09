# Thesis tables

Measured results, one finding per file, written by the `research-specialist` agent.

Each `tab-<slug>.tex` is a self-contained `\input{}` fragment: a single `table` float with
a `\caption` and `\label{tab:<slug>}`, and **no preamble**. The thesis document owns the
preamble and must load `booktabs` (plus `siunitx` if a table uses `S` columns).

```latex
\input{docs/thesis/tab-graph-build-cost}
```

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
