# Use Cases: Study Modes (UC-10 – UC-14)

Detail for [`unilearn-spec.md`](../unilearn-spec.md) §3. Actor: a **host agent** (MCP tools) conversing with the student; CLI equivalents where noted. The professor-facing modes (UC-20–24) and photo notes (UC-15) of the archived iteration are dropped.

---

## UC-10 — Verified mock tests

**Preconditions:** subject indexed. Thick path needs a generation provider; thin path needs none.

**Main flow**

1. The student asks their host agent for a quiz (topic, difficulty, count, types: MCQ / short answer / code-completion / true-false-justify).
2. **Thick:** host calls `generate_quiz` → UniLearn generates from retrieved context and runs the verifier loop (max 2 retries per question, then drop + batch report). **Thin:** the host generates from `search` results and calls `submit_questions` → same verifier; rejections return machine-readable reasons (`not_answerable_from_chunks`, `wrong_answer_key`, `distractor_not_wrong`, `reference_solution_failed`) and the host regenerates.
3. **Verifier, per question:** answerable from cited chunks alone (re-retrieval); answer key correct; distractors actually wrong; **code questions: reference solution executed in a subprocess** (timeout, tempdir) — output must match.
4. The host presents the quiz conversationally; `submit_quiz` records per-question results → mastery (UC-14).
5. Weak-area mode: `generate_quiz(weak_areas=true)` weights retrieval toward the student's weak graph communities.

**Acceptance criteria**

- No unverified question ever enters `store.db`, from either path; every stored question's `generation_source` is recorded.
- A generated code question's reference solution compiles/runs and matches its stated answer (proven by execution, not model opinion).
- A thin-path rejection round-trips through a real host agent to an accepted regeneration.

---

## UC-11 — Verified flashcards → Anki

1. `generate_deck` (thick) or `submit_cards` (thin) → verifier gate → deck stored in `store.db` (exported with the KB — one student pays verification, the course imports the deck).
2. `export_deck` → **`.apkg` (genanki)** — Anki owns daily spaced repetition; UniLearn owns verified generation. No in-chat SRS.

**Acceptance criteria:** an exported deck imports into stock Anki with cards, answers, and source citations on the back; every card cites resolving chunks.

---

## UC-12 — Graph study formats

**Preconditions:** graph built (skippable at index time; these tools report "graph not built — run `unilearn index --graph`" otherwise).

- `overview(subject, topic)` — community-summary synthesis ("main themes", exam-scope overviews). Fires graphrag global search — router-gated / explicit only (cost).
- `drill_down(subject, entity)` — entity-anchored local search for multi-hop questions.
- Citation rule holds: summaries provide breadth; **citations always resolve to verbatim chunks** (a summary has no page).

**Acceptance criteria:** an overview answer names its constituent communities and cites verbatim chunks; both tools respond usefully on the pilot subjects' graphs.

---

## UC-13 — Coding challenges

1. Host requests challenges for a topic → generated from indexed lab/lecture content (thick or thin path).
2. Every challenge ships with tests + a reference solution **proven by subprocess execution** before storage (same runner as UC-10 code questions; timeout + tempdir).
3. The student solves in their own environment — their host agent is already a coding agent; UniLearn deliberately does not tutor code itself (dropped native code tutor, pivot #2).

**Acceptance criteria:** a stored challenge's reference solution passes its own tests via the runner; a broken generation is rejected with `reference_solution_failed`.

---

## UC-14 — Mastery & study memory

**Mastery:** per-community mastery = quiz results (`progress.db`) joined to the graph's Leiden communities — the same graph that serves retrieval structures progress. `mastery_report` (MCP) returns the map; the static dashboard (P7) renders it.

**Study memory (.remember-style):**
- `recent_activity(subject)` — SQL rollup of traces + quiz events **by day** (stdio sessions have no observable end).
- `remember(subject, note)` / recall — host-written qualitative notes; recalled as **layer-4 data**.
- `continue-studying` (MCP prompt) — bundles recent activity + notes + mastery for a one-command warm start in any host.
- No server-side LLM summarization: the consumer is an LLM; it narrates structured rollups on demand.

**Privacy:** all of UC-14 lives in `progress.db` — never exported.

**Acceptance criteria:** quiz results move the mastery map; a new host session opened with `continue-studying` correctly names the previous day's topics and weak areas without any LLM call server-side.
