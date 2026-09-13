# Groundly

Local-first course knowledge bases for AI agents — index course materials, serve them to MCP hosts (Claude Code/Codex/Desktop) as cited chunks, verify what they generate back, and share the result as one file. Bachelor thesis project. Pitch and status: [README.md](README.md).

## Where things are decided

- **Master spec + document map:** [docs/groundly-spec.md](docs/groundly-spec.md) — §4 component decisions, §7 decision register, §8 roadmap.
- **Use-case contracts (acceptance criteria = "done"):** [docs/use-cases/](docs/use-cases/knowledge-base.md)
- **Architecture:** [overview](docs/architecture/overview.md) · [data-model + interchange](docs/architecture/data-model.md) · [retrieval](docs/architecture/retrieval.md) · [verification & trust](docs/architecture/agents.md)
- **Stack + LLM provider boundary:** [docs/tech-stack/tech-stack.md](docs/tech-stack/tech-stack.md)
- **Distribution / security / costs:** [docs/infrastructure/](docs/infrastructure/distribution.md)
- **Measurements:** [docs/thesis/experiments.md](docs/thesis/experiments.md) — protocols and results for the retired experiments; their code is at tag `thesis-experiments-2026-09`.

## Working rules

Binding invariants auto-load from `.claude/rules/` (module boundaries, grounding guarantees, conventions). Docs are the source of truth — a decision change updates the docs in the same change set (use `/decision`). Implement use cases with `/implement-uc UC-XX`; review with the `spec-guardian` and `security-reviewer` agents. Commit finished, reviewed work on a feature branch — never commit to `main`.

## Agent skills

### Issue tracker

Issues live in GitHub Issues (github.com/paulhondola/groundly), via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context — decisions live in the existing docs, not a fresh CONTEXT.md. See `docs/agents/domain.md`.

## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost)
