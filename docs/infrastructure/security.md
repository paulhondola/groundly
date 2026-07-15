# Security & Privacy Model

Single-user, local-first: most of the archived iteration's threat model (multi-tenant isolation, sandbox escape reaching other students' data, upload abuse) dissolved with the server. What remains is small, specific, and real. Ordered by exposure.

## 1. Import — the trust boundary

A `.unilearn` bundle is third-party content that will enter the student's prompts and filesystem.

**Controls:**
- **Zip-slip protection**: extraction rejects entries escaping the target directory (no absolute paths, no `..`); symlinks not extracted.
- **Manifest validation before anything is read**: format version supported, counts sane; unknown schema versions refused (`PRAGMA user_version` check on the imported store.db).
- **Imported chunks, graph summaries, and subject profiles are layer-4 data** — delimited, quoted, never instructions ([`../architecture/agents.md`](../architecture/agents.md)). Imported subject profiles additionally inherit the layer-2 caps: size-capped, cannot alter grounding rules.
- Imported SQLite files are opened with the same schema checks as native ones; no code paths execute content from the bundle.

## 2. Prompt injection via documents

The student's *own* lecture PDFs are as capable of carrying "ignore previous instructions" as an import. All retrieved content — chunks, summaries, recalled `remember()` notes — is layer-4: instructions inside it are inert by construction of the immutable system layer. A profile or note can never disable citations or the refusal path.

## 3. Subprocess execution (verifier + coding challenges)

The verifier executes LLM-generated reference solutions; challenges run student-visible code. This is the student's own machine running code produced by the student's own chosen model — self-risk, but bounded anyway:

**Controls:** temp working directory, wall-clock timeout, output size cap, no shell interpolation of generated strings (argv exec). No network isolation is claimed — documenting that honestly beats pretending a sandbox exists. (The archived gVisor design existed because *our server* ran *other people's* code; that premise is gone.)

## 4. Local servers

`unilearn serve` (MCP-over-HTTP + dashboard) binds **127.0.0.1 only** — no-auth is acceptable exactly and only on loopback. Refuse `--host` values other than loopback without an explicit `--i-know-what-im-doing` style override. stdio MCP has no network surface at all.

## 5. Privacy

- **Nothing leaves the machine** except calls to the student's own configured LLM provider (their key, their choice) and model downloads from Hugging Face.
- **The privacy boundary is a file**: `progress.db` — every query (traces), quiz result, and study note — is never exported. `store.db` exports carry the whole knowledge base including chunk text and original materials; the export UX says so plainly ("this bundle contains everything indexed in this subject").
- **Sharing = sharing course-material text.** Between enrolled students this is note-sharing; UniLearn documents it rather than policing it (thesis acknowledges the copyright surface).
- No telemetry, no accounts, no third-party trace storage (LangSmith was dropped for exactly this reason).

## Residual risks, named

- A malicious `.unilearn` bundle with a crafted SQLite file targeting parser bugs — mitigated by schema checks, not eliminated.
- Docling parsing a hostile PDF (from a merge-by-reindex of imported materials) — contained to the extraction subprocess (`extraction_failed`), not the app.
- Generated code doing something hostile inside the timeout — accepted as self-risk on the student's own machine, stated in docs.
