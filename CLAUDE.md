# Groundly

Local-first course knowledge bases for AI agents — index course materials, serve them to MCP hosts (Claude Code/Codex/Desktop) with enforced grounding, verified generation, and a portable interchange format. Bachelor thesis project. Pitch and status: [README.md](README.md).

## Where things are decided

- **Master spec + document map:** [docs/groundly-spec.md](docs/groundly-spec.md) — §4 component decisions, §7 decision register, §8 phasing (P1–P7).
- **Use-case contracts (acceptance criteria = "done"):** [docs/use-cases/](docs/use-cases/knowledge-base.md)
- **Architecture:** [overview](docs/architecture/overview.md) · [data-model + interchange](docs/architecture/data-model.md) · [retrieval](docs/architecture/retrieval.md) · [agents](docs/architecture/agents.md)
- **Stack + LLM provider boundary:** [docs/tech-stack/tech-stack.md](docs/tech-stack/tech-stack.md)
- **Distribution / security / costs:** [docs/infrastructure/](docs/infrastructure/distribution.md)

## Working rules

Binding invariants auto-load from `.claude/rules/` (module boundaries, grounding guarantees, conventions). Docs are the source of truth — a decision change updates the docs in the same change set (use `/decision`). Implement use cases with `/implement-uc UC-XX`; review with the `spec-guardian` and `security-reviewer` agents. Commit finished, reviewed work on a feature branch — never commit to `main`.
