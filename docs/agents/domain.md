# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

This repo does not use the generic `CONTEXT.md` / `docs/adr/` convention. Decisions and
architecture already live in a documented map — read these instead:

## Before exploring, read these

- **`docs/groundly-spec.md`** — master spec: §4 component decisions, §7 decision register, §8 phasing (P1–P7). The canonical source for "why is it built this way."
- **`docs/use-cases/`** — acceptance criteria for each use case (UC-XX). "Done" = these pass.
- **`docs/architecture/overview.md`**, **`data-model.md`**, **`retrieval.md`**, **`agents.md`** — component-level architecture.
- **`docs/tech-stack/tech-stack.md`** — stack + LLM provider boundary.
- **`.claude/rules/`** — binding invariants (module boundaries, grounding guarantees, conventions), auto-loaded every session.

## Recording a new decision

Use the `/decision` skill — it updates `docs/groundly-spec.md` §4/§7 and any satellite docs in the same change set. Don't create ad-hoc ADR files.

## Use the spec's vocabulary

When your output names a domain concept, use the term as defined in `docs/groundly-spec.md` / the architecture docs. Don't drift to synonyms.

## Flag doc conflicts

If your output contradicts an existing decision, surface it explicitly rather than silently overriding:

> _Contradicts decision in §7 (docs/groundly-spec.md) — but worth reopening because…_
