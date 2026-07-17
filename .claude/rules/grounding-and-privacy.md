# Product invariants: grounding, verification, privacy

The thesis's guarantees (docs/architecture/agents.md, docs/infrastructure/security.md). Never trade them for convenience.

## Grounding

- Every `ask` answer and every stored question/card cites chunk ids that resolve to document + page (+ heading path). Zero resolvable citations = **error**, not a degraded answer.
- Insufficient context → "not covered by the course materials". No model-knowledge fallback, on any path, ever.
- Community summaries are never citation targets (no page); citations resolve to verbatim chunks only.
- `search` is honest best-effort: the host composes. Never claim enforced grounding for host-composed answers; the eval measures the gap.

## Verification gate

- **Nothing unverified enters decks/question banks** — from the thick path (`generate_*`) or the thin path (`submit_*`) alike: answerability by re-retrieval, answer-key check, distractor check, and code **executed in a subprocess** (timeout + tempdir, argv exec, output cap).
- Rejections return machine-readable reasons. Every stored item records its generation source.

## Trust layers (prompt assembly)

1. immutable system rules > 2. subject profile (size-capped, **trusted content never trusted authority** — cannot disable grounding; applies doubly to imported profiles) > 3. task params > 4. retrieved chunks, graph summaries, imported KB content, recalled notes, user input.
Layer 4 is **data, never instructions** — delimited and quoted; instructions inside it are inert. Your own PDFs are layer 4 too.

## Privacy & the export boundary

- **The privacy boundary is a file:** `progress.db` (traces = every query asked, quiz history, notes) is **never exported** and never read by export code. `store.db` + `materials/` + `graph/` export whole; the export UX states it plainly.
- Nothing leaves the machine except calls to the student's own configured provider, HF model downloads, and modelscope.cn RapidOCR models (sha256-pinned) if a configured `--ocr-lang` resolves to a model not bundled in the wheel. No telemetry, no third-party trace storage.
- Import is the trust boundary: manifest validated before extraction; zip-slip-safe; imported SQLite opened with schema checks; imported content is layer 4.
