# Use Cases: Study Modes

Detail for [`groundly-spec.md`](../groundly-spec.md) §3. Actor: a **host agent** (MCP tools) conversing with the student. A learning session stays in the host plus Groundly's local page (decision 38).

## Planned

Each gets its own spec, with acceptance criteria written here, before implementation.

| Use case | Sub-project | Note |
|---|---|---|
| **Topic tree** | 2 | A roadmap-style map of each subject, built by the host from the chunks' heading paths and anchored to cited chunks; no graph, no extra model (decision 37). Lives in `store.db`, so it travels with bundles. Served as a local page with progress per topic. |
| **Verified quizzes + mastery** | 3 | Multiple-choice and open questions per topic, submitted through the verifier gate; answer-key and distractor checks are structural (no provider). Answers are recorded in `progress.db`, never exported. A topic is mastered at ≥90% correct over its last 10 answers (at least 10 answered); a parent is mastered when all its subtopics are. Code questions are refused at the door. |

## Ideas, not planned

- **Coding challenges** — code questions checked by running a reference solution in a subprocess runner (timeout, tempdir, argv exec). Most tractable for Python courses. Until a runner exists, no code question may enter `store.db`.
