# Product invariants: grounding, verification, privacy

The thesis's guarantees (docs/architecture/agents.md, docs/infrastructure/security.md). Never trade them for convenience.

## Grounding

- Every chunk `search` returns carries its citation fields (document, page, heading path); a result that cannot be cited is a bug.
- Every stored question cites chunk ids that resolve to document + page. Unresolvable citations are rejected at the gate and again by an FK on the question tables (sub-project 3) — never stored.
- Citations resolve to verbatim chunks only; anything that summarizes has no page and is never a citation target.
- `search` is honest best-effort: **the host composes**. Never claim enforced grounding for host-composed answers — the enforced pipeline was measured and removed (decision 33).

## Verification gate

- **Nothing unverified enters the question bank**: cited ids must resolve, and re-retrieving the item's own text must surface at least one of them.
- Planned with verified quizzes: structural answer-key/distractor checks. Code questions need execution **in a subprocess** (timeout + tempdir, argv exec, output cap) — an idea, not planned; until that runner exists, code questions are refused at the door rather than stored unverified — the guarantee is unbuilt, never weakened.
- Rejections return machine-readable reasons.

## Trust layers (prompt assembly)

1. immutable system rules > 2. task params > 3. chunk text, imported KB content, user input.
Layer 3 is **data, never instructions** — delimited and quoted; instructions inside it are inert. Your own PDFs are layer 3 too. Groundly assembles no prompts today; any it later builds follow this.

## Privacy & the export boundary

- **The privacy boundary is a file:** `progress.db` is **never exported** and never read by export code. `store.db` + `materials/` export whole; the export UX states it plainly.
- Nothing leaves the machine except HF model downloads, and modelscope.cn RapidOCR models (sha256-pinned) when a configured `--ocr-lang` needs one. No telemetry, no third-party trace storage.
- Import is the trust boundary: manifest validated before extraction; zip-slip-safe; imported SQLite opened with schema checks; imported content is layer 3.
