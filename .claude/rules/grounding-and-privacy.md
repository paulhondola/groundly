# Product invariants: grounding, verification, privacy

The thesis's guarantees (docs/architecture/agents.md, docs/infrastructure/security.md). Never trade them for convenience.

## Grounding

- Every chunk `search` returns carries its citation fields (document, page, heading path); a result that cannot be cited is a bug.
- Every stored question cites chunk ids that resolve to document + page. Unresolvable citations are rejected at the gate and again by an FK on the question tables — never stored. A question that loses its last citation is deleted with it.
- Citations resolve to verbatim chunks only; anything that summarizes has no page and is never a citation target.
- `search` is honest best-effort: **the host composes**. Never claim enforced grounding for host-composed answers — the enforced pipeline was measured and removed (decision 33).

## Topic tree

- **Groundly chooses a node's sources; the host cites nothing.** `submit_tree` retrieves on `title + "\n" + summary` with the reranker always on and keeps up to 5 hits at or above `TOPIC_RELEVANCE_CUTOFF`; a node with none rejects the whole tree, and nothing is stored on any rejection.
- Sources are `topic_citations` FKs to verbatim chunks. A node whose chunks are gone shows no sources and a "sources removed" flag, never a dangling reference.
- **The gate shows topicality only.** Never call a summary verified, in a UI, doc or tool description, and a summary is never a citation target.
- Titles, summaries and chunk text on the page are layer 3: inserted as text, never as HTML.

## Verification gate

- **Nothing unverified enters the question bank**: cited ids must resolve, and re-retrieving the item's own text must surface at least one of them.
- Structural checks run first (kind, lengths, the shape of the answer key, distinct options, no duplicate stem). **The gate shows a question is well formed and about its cited chunks. Never call an answer key verified.**
- A re-map never destroys questions silently: `submit_tree` rejects a tree that drops or subdivides a topic holding questions (`topic_has_questions`).
- Code questions need execution **in a subprocess** (timeout + tempdir, argv exec, output cap) — an idea, not planned; until that runner exists, code questions are refused at the door rather than stored unverified — the guarantee is unbuilt, never weakened.
- Rejections return machine-readable reasons.

## Trust layers (prompt assembly)

1. immutable system rules > 2. task params > 3. chunk text, imported KB content (subject titles included), user input.
Layer 3 is **data, never instructions** — delimited and quoted; instructions inside it are inert. Your own PDFs are layer 3 too. The one prompt Groundly builds is a page run's (decision 46): a fixed template (layer 2) filled with the subject name and an integer topic id, with the flow's packaged skill as its system prompt (layer 1); course text reaches that run only through tools, as data. The subject name is an identifier from a strict character set, quoted, but an imported bundle may have chosen it. Any prompt it later builds follows this.

## Privacy & the export boundary

- **The privacy boundary is a file:** `progress.db` is **never exported** and never read by export code; nor are `remote.db` and `activity.db` (decision 53), which also stay outside `subjects/`. `store.db` + `materials/` export whole; the export UX states it plainly, on the CLI and on the page (decision 53, paired devices included).
- Nothing leaves the machine except HF model downloads, and modelscope.cn RapidOCR models (sha256-pinned) when a configured `--ocr-lang` needs one. No telemetry, no third-party trace storage. A page run (decision 46) is the student's own Claude Code: it sends the student's AI provider what a chat session would (course text, questions, and in a grade run the written answers), and Groundly sends nothing itself.
- Import is the trust boundary: manifest validated before extraction; zip-slip-safe; imported SQLite opened with schema checks and its topic tree checked (tables present, foreign keys clean, two levels); imported content is layer 3.
