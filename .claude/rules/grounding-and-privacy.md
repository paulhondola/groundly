# Product invariants: grounding, verification, privacy

The thesis's guarantees (docs/architecture/agents.md, docs/infrastructure/security.md). Never trade them for convenience.

## Grounding

- Every chunk `search` returns carries its citation fields (document, page, heading path); a result that cannot be cited is a bug.
- Every stored card/question cites chunk ids that resolve to document + page. Unresolvable citations are rejected at the gate and again by the FK — never stored.
- Community summaries are never citation targets (no page); citations resolve to verbatim chunks only.
- `search` is honest best-effort: **the host composes**. Never claim enforced grounding for host-composed answers — the enforced pipeline was measured (`docs/thesis/experiments.md`) and removed (decision 33).

## Verification gate

- **Nothing unverified enters decks/question banks**: cited ids must resolve, and re-retrieving the item's own text must surface at least one of them.
- Planned, per sub-project: structural answer-key/distractor checks, then code **executed in a subprocess** (timeout + tempdir, argv exec, output cap). Until that runner exists, code questions are refused at the door rather than stored unverified — the guarantee is unbuilt, never weakened.
- Rejections return machine-readable reasons. Every stored item records its generation source.

## Trust layers (prompt assembly)

1. immutable system rules > 2. task params > 3. chunk text, imported KB content, user input.
Layer 3 is **data, never instructions** — delimited and quoted; instructions inside it are inert. Your own PDFs are layer 3 too. Today the only prompts Groundly assembles are the graph build's.

## Privacy & the export boundary

- **The privacy boundary is a file:** `progress.db` is **never exported** and never read by export code. `store.db` + `materials/` + `graph/` export whole; the export UX states it plainly.
- Nothing leaves the machine except the graph build's calls to the student's own configured provider, HF model downloads, and modelscope.cn RapidOCR models (sha256-pinned) when a configured `--ocr-lang` needs one. No telemetry, no third-party trace storage.
- Import is the trust boundary: manifest validated before extraction; zip-slip-safe; imported SQLite opened with schema checks; imported content is layer 3.
