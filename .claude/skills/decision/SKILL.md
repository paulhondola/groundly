---
name: decision
description: Record or change a Groundly architecture decision consistently across all docs. Use when the user decides, changes, or reverses a tech choice, scope item, or invariant — e.g. "we're switching to Qdrant" or "drop the JupyterLab stretch goal".
---

# Record an architecture decision

The docs are decision-complete: every choice is stated once as a decision with its decisive reason, never as a menu. Keep it that way.

## Steps

1. **Locate every statement of the affected decision.** Grep `docs/` for the topic. The usual homes: `groundly-spec.md` §4 (component table) and §7 (resolved decisions), plus exactly one satellite doc that owns the detail (`tech-stack/tech-stack.md`, `architecture/*.md`, or `infrastructure/*.md`). Check `.claude/rules/` too — invariants live there in condensed form.

2. **Capture the why.** A decision entry needs: the choice, the decisive reason, and consequences (cost, privacy, migration path). If the user gave no reason, ask for one — "changed our minds" is not auditable in a thesis.

3. **Update all statements in one pass.** Spec table row, spec §7 entry, the owning satellite doc, and any rule file. The old choice becomes the documented alternative/migration path if it remains viable; delete it otherwise.

4. **Check for cascade effects.** Does the change alter the cost model (`infrastructure/cost-model.md`), the distribution story (`infrastructure/distribution.md`), the threat model (`infrastructure/security.md`), the interchange format (`architecture/data-model.md` — a manifest/pin change breaks import compatibility), or an acceptance criterion? Update those too or explicitly note why they're unaffected.

5. **Update project memory** (`groundly-architecture-decisions` memory file) so future sessions treat the new decision as settled.

6. **Report** the changed files and the one-line form of the new decision.
