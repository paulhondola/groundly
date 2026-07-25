# P6 slice 1 — Verifier gate + UC-11 verified flashcards → Anki

**Status:** approved 2026-07-25. First P6 sub-project; UC-10/13/14 follow in later slices.
**Done =** UC-11 acceptance criteria in [docs/use-cases/student-modes.md](../../use-cases/student-modes.md): an exported deck imports into stock Anki with cards, answers, and source citations on the back; every card cites resolving chunks; no unverified card ever enters store.db from either path.

## Scope

- Schema: `decks`, `questions`, `question_citations` in store.db (user_version 1 → 2, additive migration). `quiz_events`/`notes` stay out (UC-10/14 slices).
- Verifier gate (`agents/verifier.py`): all four canonical rejection reasons declared; this slice implements citation resolution + answerability-by-re-retrieval. Answer-key / distractor / code-execution checks arrive with UC-10/13 — inside `verify_card`, without changing its signature or the `Rejection` contract.
- Thin door: `submit_cards` MCP tool (host-generated cards, zero-key).
- Thick door: `generate_deck` MCP tool behind an in-memory job-id pattern + `get_job`; two-phase cost confirm.
- Export: `export_deck` (MCP tool + CLI verb) → `.apkg` via genanki.

## Design

### Schema (store.db v2)

```sql
CREATE TABLE decks (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE questions (
    id INTEGER PRIMARY KEY,
    deck_id INTEGER REFERENCES decks(id) ON DELETE CASCADE,
    type TEXT NOT NULL CHECK (type IN ('flashcard','mcq','short_answer','code','true_false_justify')),
    body TEXT NOT NULL,              -- flashcard front
    answer TEXT NOT NULL,            -- flashcard back / answer key
    distractors TEXT,                -- JSON array; NULL for flashcards
    verify_status TEXT NOT NULL DEFAULT 'verified' CHECK (verify_status IN ('verified')),
    generation_source TEXT NOT NULL CHECK (generation_source IN ('server','host')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_questions_deck ON questions(deck_id);
CREATE TABLE question_citations (
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    PRIMARY KEY (question_id, chunk_id)
);
```

- Flashcard = `questions` row with `type='flashcard'` (data-model.md's "questions / decks" read literally; UC-10 lands in the same table with zero churn).
- Deck membership = nullable `deck_id` FK (nothing needs many-to-many; future quiz questions are deck-less).
- Citations = FK rows, because data-model.md requires "enforced at the verifier gate AND by FK" — a JSON column can't FK.
- Migration: `_MIGRATIONS = {2: _SCHEMA_V2}` applied in `connect()` after the existing newer-than-known refusal; additive only, **no interchange format_version bump** (P5 corpus_hash precedent). Imported v1 bundles upgrade on first open; older groundly refuses v2 stores (already-documented behavior).
- `remove_material` addendum: after chunk deletion cascades, `DELETE FROM questions WHERE id NOT IN (SELECT question_id FROM question_citations)` — a card stripped of all citations must not survive (zero resolvable citations = error, by rule).

### Verifier (`groundly/agents/verifier.py`)

```python
REJECTION_REASONS = ("not_answerable_from_chunks", "wrong_answer_key",
                     "distractor_not_wrong", "reference_solution_failed")

@dataclass
class CardCandidate:
    front: str
    back: str
    chunk_ids: list[int]

@dataclass
class Rejection:
    reason: str   # one of REJECTION_REASONS — the machine-readable contract
    detail: str   # specific cause, host/human-readable

def verify_card(card, store, *, embedder=None) -> Rejection | None
```

Checks, fail-fast, cheapest first:
1. **Citation resolution** — empty `chunk_ids` or any id absent from `store.chunk_details()` → `not_answerable_from_chunks` (a chunk that doesn't resolve cannot answer anything; the specific cause goes in `detail`).
2. **Answerability by re-retrieval** — query = `front + "\n" + back`; `VectorRetriever(rerank=False, context_k=VERIFY_TOP_K)` with `VERIFY_TOP_K = 20`; pass iff ≥1 cited chunk id appears in the top-K RRF result. Membership test, no similarity threshold (thresholds need calibration; membership is boring and robust). No reranker — a second lazy model load the gate doesn't need.

Zero-key: only bge-m3 is touched, lazily on first call (never at MCP spawn). `embedder=` injectable for stub tests. Verifier retrieval writes **no** search trace (internal machinery, not student activity).

### Thin door (`groundly/agents/decks.py`)

`submit_cards(subject, deck, cards, *, generation_source) -> list[CardOutcome]` — the single gate both doors call. Accepted → `add_verified_card` (question + citation rows, one transaction; the FK is the second enforcement). Rejected → machine-readable `Rejection` in the outcome, nothing stored. MCP tool hardcodes `generation_source='host'`; return shape `{deck, accepted: [{index, question_id}], rejected: [{index, reason, detail}]}`.

**Rejection-rate durability:** one `verifications` table in progress.db (`generation_source`, `reason` NULL-for-accepted, `ts`), created idempotently in `connect_progress`, one insert per verdict — the rejection-rate-by-source thesis measurement needs durable verdicts from both doors. progress.db never travels → no interchange impact. (data-model.md updated in this change set.)

### Jobs (`groundly/agents/jobs.py`)

In-memory `_JOBS: dict[str, Job]`, daemon `threading.Thread`, one global `_GEN_LOCK`. States: queued → running → done/failed; `Job.report` = batch report. The whole downstream stack is synchronous — a thread is the plain bounded loop the docs ask for, identical on stdio and HTTP.

- Jobs are **session-scoped**: cards commit per-card, so a killed host session loses only the job record, never verified cards (agents.md gets a note).
- ponytail: global lock serializes all thick jobs (docs require it only for local providers); per-provider check if parallel cloud jobs ever matter.
- ponytail: in-memory jobs, lost on restart; move to progress.db if resumable jobs are ever needed.

### Thick door (`generate_deck` + `get_job` MCP tools)

Two-phase confirm (the MCP reading of "cost estimates before spending"):
- `confirm=false` (default): returns `{estimated_tokens, estimated_cost_usd, note}` from a constants-only heuristic (`GEN_CONTEXT_K*512 + 400` prompt, `count*80` output; unpriced provider → null estimate + explicit note). No retrieval, no model load, no job.
- `confirm=true`: `require_provider("generation")` fail-fast in the handler, then `start_job` → `{job_id, status: "queued"}`.

Job body (sync, run by the job thread): retrieve topic context once (`GEN_CONTEXT_K = 20`, no rerank, no search trace) → `assemble_cards` prompt (`prompts.py`, reuses `_render_chunks`/`_escape`; chunks stay inside the delimited layer-4 block; `CARD_SYSTEM_RULES` demand a JSON array `[{front, back, chunk_ids}]`, citing source chunks, emit-fewer-not-invent) → one `complete("generation", …)` for the whole batch (`count` ≤ 50) → parse (fenced-block tolerant; unparseable = one burned attempt) → every card through `submit_cards(source='server')` → rejected cards get one follow-up call per retry round with their rejection reason + detail quoted back, same materials block. **Max 2 retries per card (3 attempts), then dropped with a batch-report note; ≤3 LLM calls per job.**

Batch report: `{deck, requested, accepted, dropped: [{front, reason, detail, attempts}], tokens, cost_usd}`. One trace row per job (`kind='ask'`, `arm='generate_deck'` — P5 precedent; the traces CHECK constraint on existing progress.dbs can't grow cheaply).

### Export (`groundly/core/anki.py` + wrappers)

`export_deck(subject, deck_name, out_path=None) -> Path` via genanki (already pinned `>=0.13` in pyproject):
- Model: constant `_MODEL_ID`, fields `Front`/`Back`/`Sources`, back template `{{Back}}<hr id=sources><div class=sources>{{Sources}}</div>` — citations on the back, per acceptance criterion.
- Deterministic ids: deck id = first 4 bytes of `sha256("groundly/<subject>/<deck>")` (31-bit safe); note guid = `genanki.guid_for(subject, deck, question_id)` → re-export updates in place, never duplicates.
- `Sources` = one line per citation: `filename, p.N — heading_path` (from `store.deck_cards()` join).
- Surfaces: MCP tool writes `~/.groundly/<subject>/exports/<deck>.apkg` (outside the bundle allowlist; absolute path returned); CLI verb `export-deck SUBJECT DECK [--out PATH]` defaults to `./<deck>.apkg`. Both ~15-line wrappers over the one function.
- Empty/unknown deck → specific error naming `list_decks`.

## Test strategy

Stub-based throughout (no real models, no containers): migration tests (v1 → v2 upgrade, FK rejection of bogus chunk ids, orphan cleanup); verifier contract tests on the existing retrievable-subject fixture with a deterministic injected embedder; thin-path row-count assertions (rejected stores nothing) + zero-provider round-trip; thick-loop tests with scripted `StubChat` replies (retry prompt carries the rejection reason, max-retries drop, unparseable JSON burns an attempt); job serialization test; `.apkg` validation by opening the zip's `collection.anki2` with stdlib sqlite3 and asserting front/back/citation text in `notes.flds` + determinism across re-exports.

## Decisions recorded with this slice

1. MCP cost-confirm = the two-phase `confirm` parameter (conventions' "print estimate before spending" for a promptless surface).
2. Jobs are session-scoped; durability lives in per-card commits (agents.md note).
3. All thick jobs serialize (stricter than the local-only requirement; one lock, zero cost for a single student).
4. `verifications` table added to progress.db's documented tables (rejection-rate-by-source metric).
5. Flashcards are `questions` rows (`type='flashcard'`) — data-model.md's "questions / decks" read literally.
