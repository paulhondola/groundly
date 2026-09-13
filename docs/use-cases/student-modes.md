# Use Cases: Study Modes

Detail for [`groundly-spec.md`](../groundly-spec.md) §3. Actor: a **host agent** (MCP tools) conversing with the student.

---

## UC-11 — Verified flashcards → Anki  *(shipped)*

1. The host searches the subject, writes cards, and calls `submit_cards(subject, deck, cards)` with each card's `chunk_ids`.
2. **The verifier gate:** every cited id must resolve to a chunk in this subject, and re-retrieving the card's own front+back text must surface at least one of them. Rejections carry a machine-readable `reason` plus a `detail`; the host fixes and resubmits only those.
3. Accepted cards are stored in `store.db` with their `generation_source` — they travel with the bundle, so one student's verification serves the course.
4. `export_deck` → **`.apkg` (genanki)**. Anki owns daily spaced repetition; Groundly owns verified generation. No in-chat SRS.

**Acceptance criteria**

- An exported deck imports into stock Anki with cards, answers and source citations on the back; every card cites resolving chunks.
- `submit_cards` and `export_deck` work with no provider configured.
- A rejection round-trips through a real host agent to an accepted resubmission.

---

## Planned

Each gets its own spec before implementation.

| Use case | Sub-project | Note |
|---|---|---|
| **UC-10 verified mock tests** | 2 | `submit_questions` through the same gate; answer-key and distractor checks are structural (no provider). Code questions are refused until the UC-13 runner exists. |
| **Topic map** | 3 | Level-0 graph communities become the course's theme layer: a `topics` tool serving community summaries with chunk citations, and themes in `export-graph`. Replaces the retired `overview`/`drill_down` tools, which queried the graph per question. |
| **UC-13 coding challenges** | 4 | Needs the subprocess runner (timeout, tempdir, argv exec). Until it exists, no code question may enter `store.db`. |
| **UC-14 mastery & study memory** | 4 | Anki note tags + AnkiConnect review history (FSRS retrievability) joined to quiz results, rolled up per theme and per material, plus coverage gaps. Lives in `progress.db`, never exported. |
