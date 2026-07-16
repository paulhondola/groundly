---
name: implement-uc
description: Implement a Groundly use case (UC-XX) driven by its documented acceptance criteria. Use when starting work on any use case from docs/use-cases/, e.g. "/implement-uc UC-02" or "let's build the mock test generator".
---

# Implement a use case

Input: a UC id (UC-01…UC-30) or feature name. The acceptance criteria in `docs/use-cases/` define "done" — not vibes.

## Steps

1. **Load the contract.** Read the UC's section in `docs/use-cases/` (knowledge-base / student-modes / sharing). Extract: preconditions, main flow, alternate/error flows, acceptance criteria. Read the referenced architecture docs for the parts this UC touches (`overview.md` for module placement, `data-model.md` for storage + interchange, `retrieval.md`/`agents.md` if applicable).

2. **Check phase order.** Spec §8 maps UCs to phases P1–P7. If this UC depends on machinery from an earlier phase that doesn't exist yet (e.g., UC-12 graph formats need the P5 graph; UC-14 mastery needs P5 communities), say so and confirm scope before coding.

3. **Restate criteria as a test list.** Each acceptance criterion and each alternate flow becomes at least one named test. Include the standing invariant tests that apply (citation resolution for anything generating content; verifier gate for anything writing decks/questions; export-boundary test for anything touching progress.db or export).

4. **Implement inside the module boundaries** (`.claude/rules/architecture.md`): client surface in `cli/`/`mcp/`, logic in the owning service module, LLM access only via `groundly/llm/`, long work as background tasks behind a job id. Tests first where the logic is non-trivial; tests use SQLite files + stub providers, no services.

5. **Verify.** Run the test list; every acceptance criterion must map to a passing test. Report the mapping explicitly (criterion → test name → pass/fail).

6. **Close the loop.** If implementation forced a deviation from the documented flow, update the UC doc in the same change set and say why. Consider running the `spec-guardian` agent on the diff before finishing.
