# Tests on the local page: question kinds, display and tracking

Design for sub-project 3 (verified quizzes and mastery). Date: 2026-11-16. Status: proposed, not yet adopted into `docs/groundly-spec.md`.

It replaces the chat-based quiz in UC-12 of [the use-case design](2026-11-09-use-cases-and-interfaces-design.md): the student takes a test on Groundly's local page, not in the host conversation.

## 1. Intent

A student who has mapped a course wants to check what they know against the course's own material, and see which topics still need work. The host agent writes the questions; Groundly keeps only those that pass its gate, shows them as a test on the local page, grades what it can, and records the results privately.

Why the page and not the chat: a test is something you sit, with every question in front of you and the feedback held back until the end. A chat reveals each answer as it goes and looks different in every host. The page works the same for Claude Code, Codex and Claude Desktop.

## 2. Goals, non-goals, anti-goals

**Goals**

- Four question kinds a student can answer on the page: single answer, multiple answer, code reading, open answer.
- A test is drawn from the verified bank for a subtopic, a big topic or the whole course.
- Single, multiple and code-reading answers are graded by Groundly at Submit. Open answers are reviewed by the host afterwards.
- Every answer is recorded in `progress.db` and drives per-topic mastery shown on the map.

**Non-goals (not in this project)**

- Live coding, where the student writes code that is run. Code questions of that kind stay refused at the door.
- A quiz asked in the chat. The planned `quiz` and `record_answer` tools are dropped.
- Fixed, named tests written by the host and shared in bundles. Can be added later on top of the bank.
- Timers, partial credit, spaced repetition, editing a stored question, any new CLI verb.

**Anti-goals (would make this a failure even if it works)**

- The page rendering HTML written by a model or taken from a bundle.
- Any wording, in the page, a doc or a tool description, that says an answer key is verified correct. The gate shows a question is about the cited material and is well formed; it cannot prove the key is right.
- An answer key or reference answer reaching the browser before Submit.
- Anything from `progress.db` entering a bundle.
- The page loading a model.

## 3. Constraints from the project

- No LLM and no key inside Groundly (decision 37). The gate uses only the local embedder.
- `progress.db` is never exported and never read by export code.
- The page binds 127.0.0.1, checks the Host header and loads no model.
- All text from the host or a bundle is layer 3: inserted as text, never as HTML.
- One tool-surface framework (FastMCP). Tool descriptions lead with when to call them; nothing ranks one tool above another server-wide.
- Dependencies point one way: `web/` and `mcp/` call services; nothing imports the client layer.

**Rules this design changes.** These need a recorded decision (`/decision`, next number 44) and edits to `.claude/rules/` in the same change set:

| Rule today | Becomes |
|---|---|
| The page "only displays"; `web/` never writes | The page writes to `progress.db` only, through a service function. It still writes no rows to `store.db`. |
| Two writers touch `store.db` (ingestion, topic gate) | Three: the question gate, reached from `submit_questions` and `remove_questions`, is added. |
| Tool surface pinned at six entries | Four more (section 8): ten on today's `main`. The pinning test is updated on purpose and lists whatever exists when this lands. |
| Schema has no migrations; an older subject is dropped and re-indexed (decision 41) | One additive upgrade from version 1 to 2 (section 7). |
| The topic gate's rejection reasons | Gains `topic_has_questions` (section 7). |
| Planned, not yet a rule (UC-12 in the 2026-11-09 design): the host asks each question in the conversation | The student takes the test on the page. |

## 4. Approaches

Three ways to build the test page were considered.

1. **The page holds the test until Submit (chosen by Paul).** The server sends the questions without keys. The browser keeps the answers and sends them all in one request at Submit; the server grades and saves the attempt in one transaction. A refresh keeps the answers (session storage); closing the tab abandons the test and records nothing, so an abandoned test never affects mastery.
2. **The server saves every answer as it is given.** Tests could be resumed after closing the tab, but half-finished attempts need expiry rules and a ruling on whether their answers count. Not chosen: more state for a case the student can avoid by finishing.
3. **The host writes the test page itself, like a chat artifact.** Dropped: it puts model-written HTML on a page whose rule is text only, it cannot write to `progress.db`, and Claude Code has no artifacts.

Earlier choices in the same conversation, all Paul's:

- **Where the test is taken:** the local page, over the chat (as previously planned) and a host-drawn artifact.
- **Who judges an open answer:** the host, afterwards, over the student marking themself. Self-marking is a flashcard, which decision 38 dropped, and would feed a self-reported mark into mastery.
- **What a test is:** drawn from the bank at start, over a fixed test written by the host.
- **Layout:** one question per screen, over all questions on one scrolling page.

## 5. Question kinds

| Kind | What the student does | Graded by | Stored as |
|---|---|---|---|
| Single answer | Picks one option | Groundly, at Submit | `single` |
| Multiple answer | Ticks every option that applies | Groundly, at Submit. Correct only if the ticked set equals the key | `multiple` |
| Code reading | Reads a code block, then picks | Groundly, at Submit | `single` or `multiple` with a `code` field |
| Open answer | Types a few sentences | The host, afterwards | `open` |

True/false is a single-answer question with two options, not a kind. Code reading is not a kind either: any choice question may carry a `code` block, shown verbatim in a `<pre>`.

A question submitted by the host:

```json
{
  "kind": "single | multiple | open",
  "stem": "the question text",
  "code": "optional code block, choice kinds only",
  "options": [{"text": "…", "correct": true}],
  "reference_answer": "open only: the answer a reviewer compares against",
  "chunk_ids": [123, 456]
}
```

Any other `kind` (including `code`, for a question the student would answer by writing code) is rejected with `code_questions_unsupported` or `unknown_kind`.

## 6. The question gate

`submit_questions(subject, topic_id, questions)` checks each question on its own. Passing questions are stored; failing ones come back with a machine-readable reason, so one bad question does not sink the batch. Checks run cheapest first.

**Target.** `topic_id` must name a node with no subtopics (a subtopic, or a big topic that has none). Mastery is counted per such node, so a question on a parent would count nowhere. Reason: `unknown_topic_id`, `not_a_leaf_topic`.

**Structure** (no model):

- `stem` 1 to 500 characters; `code` up to 2000; each option 1 to 300; `reference_answer` 1 to 1000. Reason: `invalid_text`.
- `single`: 2 to 6 options, exactly one correct. `multiple`: 3 to 8 options, at least two correct and at least one not. Reason: `wrong_answer_key`.
- Options distinct after trimming and case-folding. Reason: `duplicate_option`.
- `open`: no options, a reference answer present. Reason: `invalid_text`.
- Same stem (trimmed, case-folded) already stored for this topic, or twice in the batch. Reason: `duplicate_question`.
- At most 50 questions per call (reason `too_large`, whole call refused) and at most 200 stored per topic (reason `bank_full` for the items over the limit).

**Grounding** (the existing verifier, reused):

- `chunk_ids` holds 1 to 5 ids (more is `invalid_text`: citing everything would pass re-retrieval trivially) and every id resolves to a chunk in this subject. Reason: `not_answerable_from_chunks`, naming the missing ids.
- Re-retrieving the question's own text surfaces at least one cited chunk in the top `VERIFY_TOP_K` (20). The text is the stem, the code, and the correct options or the reference answer, joined by newlines.

Unlike the topic tree, the host does cite here: it names the chunk ids it wrote the question from, and the gate confirms them. `search` and `get_page` already return a `chunk_id` with every chunk, so the host has the ids to cite.

**What the gate does not show.** It does not prove the key is right or the distractors are wrong; that needs a model Groundly does not have. The unused reasons `distractor_not_wrong` and `reference_solution_failed` leave `REJECTION_REASONS`. The student's safeguard is the result screen, which shows the cited page beside every answer, and `remove_questions`.

**Removing a question.** `remove_questions(subject, question_ids)` deletes stored questions, all or none if any id is unknown. It is how a question with a wrong key leaves the bank: the result screen shows each question's id so the student can tell the host which one. Recorded answers to a removed question stay in `progress.db` and keep counting.

## 7. Storage

**`store.db` (exported, travels in bundles)**

| Table | Contents | Notes |
|---|---|---|
| questions | topic FK `ON DELETE CASCADE`, kind, stem, code, reference answer | ids never reused (`AUTOINCREMENT`), so recorded answers cannot come to mean another question |
| question_options | question FK cascade, position, text, correct flag | choice kinds only |
| question_citations | question FK, chunk FK, both cascade | removing a material drops its citations; a trigger deletes a question when its last citation goes, so every stored question always cites a chunk that resolves. Chunk ids are never reused, so such a question could never regain a source |

**Older subjects are upgraded in place (Paul's choice, a break with decision 41's no-migration stance).** `STORE_USER_VERSION` goes to 2. Opening a version-1 `store.db` creates the three empty tables and the trigger and sets the version, in one transaction; no existing row is touched. Dropping and re-indexing would also delete each course's reviewed map, and the map page now reads question counts, so it would break for every existing course until then. A newer-than-known version is still refused. Import accepts a version-1 bundle the same way.

**A re-map never destroys questions silently (Paul's choice).** `submit_tree` rejects, with the new reason `topic_has_questions`, a tree that would delete a topic holding questions or give such a topic subtopics. Each entry names the topic and its question count. The host either keeps that topic as a leaf under its id or removes its questions first with `remove_questions`. The check is structural and runs before any search.

Import additionally refuses a bundle whose question tables are missing, fail `PRAGMA foreign_key_check`, or hold a question that breaks the gate's structure checks and limits (section 6), since an import never passes through the gate. Import does not repeat the grounding check: it loads no model, as with the tree. The export statement becomes "…including its topic map and question bank."

**`progress.db` (never exported)**

| Table | Contents |
|---|---|
| attempts | submitted-at time, scope topic id (null for the whole course), question count |
| answers | attempt FK, question id, topic id, kind, the response (chosen option ids, or the text), correct (true, false, or null while awaiting review), review comment, reviewed-at time |

Question and topic ids are plain integers here, never foreign keys into `store.db`, so history survives a topic or question that is removed and a topic that returns under the same id. `progress.db` gets a schema version bump checked on open; an existing empty file is upgraded in place by creating the tables, since there is nothing to migrate.

## 8. MCP tools

Four are added and the chat-quiz pair is dropped.

| Tool | When the host calls it | Arguments | Returns | Hint |
|---|---|---|---|---|
| `submit_questions` | The student asks for questions or a test on a topic and the bank is thin | `subject`, `topic_id`, `questions` | accepted ids; each rejected item with its reason | — |
| `remove_questions` | The student says a stored question is wrong | `subject`, `question_ids` | the removed ids | destructive |
| `review_answers` | The student asks to grade a test. Called with no `reviews` it lists what awaits review; called with `reviews` it records verdicts | `subject`, `reviews?: [{answer_id, correct, comment}]` | awaiting answers, each with the stem, the reference answer, the student's text and the cited chunks with their `groundly://` URIs | — |
| `progress` | The student asks how they are doing or what to study next | `subject` | per topic: questions in the bank, answers recorded, recent accuracy, state | read-only |

- A verdict is correct or not, with a comment of up to 1000 characters. The comment is layer 3 and shown as text.
- A reviewed answer cannot be reviewed again (`already_reviewed`); an unknown id is `unknown_answer_id`. Verdicts in one call are applied all or none.
- `review_answers` is one tool with two uses so the surface stays at ten; the builder may split it in two if a single description cannot say clearly when to call it.
- `progress` is also how the host learns a topic's bank is thin before writing more.

The packaged skill gains two short flows: writing questions for a topic (search the topic, write questions citing the returned chunk ids, submit, rewrite the rejected ones) and grading a test (list, judge each answer against the reference answer and the cited chunks only, record).

## 9. The page

Everything is in the existing Starlette app and its vanilla script; no new dependency.

**Starting a test.** A node's panel shows its bank size and its mastery state, and a "Start test" control with a length of 5, 10 or 20 (default 10, capped by what the bank has). A big topic's test covers all its subtopics; the subject header has "Test the whole course". With no questions in scope, the panel shows the sentence to say to the host, as the empty map does today.

**The draw.** From the scope's leaf topics, taken round-robin across the topics so a wide scope is spread evenly. Within a topic: never answered first, then last answered wrong, then least recently answered. Options are shuffled in the browser and identified by id.

**Taking it.** One question per screen. A header names the subject, the scope and "Question 3 of 10". Numbered dots jump to any question and show which are answered. Back and Next move between questions; the last screen has Submit, which warns if any question is unanswered. An unanswered question is recorded as wrong. Answers are kept in session storage until Submit.

**The result.** Shown after Submit and reachable later at its own address. It shows:

- the score over graded questions, marked provisional while any open answer awaits review;
- per question: right or wrong, the student's answer, the correct answer, the question id, and its source (`file.pdf · p.17`), which opens the PDF at that page or shows the chunk text, as on the map;
- per open answer: the reference answer and source at once, and "awaiting review — say 'grade my test' to your agent" until the verdict and comment arrive;
- the mastery state of each topic the test touched.

A question removed since the attempt shows as "question removed" with the recorded result. An open answer still awaiting review when its question is removed becomes "cannot be reviewed": `review_answers` no longer lists it, and it is left out of the score, the provisional mark and mastery. This is worked out on read; nothing is stored for it.

**History.** The subject page lists past attempts: date, scope, score, and whether any answer still awaits review.

**Requests**

| Request | Does |
|---|---|
| `GET /api/subjects/{s}/test?topic=&n=` | draws a test; returns stems, code, option ids and texts. No keys, no reference answers |
| `POST /api/subjects/{s}/attempts` | grades and saves one attempt; returns its id |
| `GET /api/subjects/{s}/attempts` and `…/attempts/{id}` | the history and one result, with keys and sources |
| `GET /api/subjects/{s}/tree` (existing) | gains each node's bank size and mastery state |

**Safety of the one write.** The `POST` is refused unless its `Origin` header is the page's own origin and its content type is JSON, on top of the existing Host check, so another site open in the browser cannot submit an attempt. The body is validated before anything is written: every question id exists in this subject and appears once, every option id belongs to its question, at most 20 answers, open text at most 4000 characters. Grading uses the keys in `store.db`, never anything the browser sent.

## 10. Mastery

- Counted per leaf topic, over its graded answers in time order. An answer awaiting review does not count until it is reviewed.
- **Mastered:** at least 10 graded answers and at least 9 of the last 10 correct.
- **In progress:** at least one graded answer; the node shows "7 of last 10 correct" (or "3 answered").
- **Not started:** none.
- A big topic with subtopics is mastered when all of them are, and otherwise shows "2 of 5 subtopics mastered".
- Computed on read from `progress.db`; nothing is stored.

## 11. Where the code goes

| Piece | Place |
|---|---|
| Gate: structure checks, then the existing grounding check | `groundly/agents/verifier.py` (`CardCandidate` and `verify_card` become the question forms) |
| Draw, grading, mastery, review | a new service module under `groundly/agents/`, called by both `web/` and `mcp/` |
| Question tables and accessors | `groundly/core/store.py` |
| Attempt and answer tables and accessors | `groundly/core/progress.py`, kept out of `store.py` |
| Tools | `groundly/mcp/server.py` |
| Routes and views | `groundly/web/app.py`, `static/page.js`, `static/page.css` |

## 12. Build order and what "done" means

One project, three slices, each reviewed with `spec-guardian` and `security-reviewer` before the next.

**Slice 1: the bank and its gate.** Tables, the gate, `submit_questions`, `remove_questions`, export and import checks.

1. A well-formed question of each kind citing real chunks is stored; each structural fault is rejected with its reason and nothing is stored for that item.
2. A question citing an unknown chunk, or whose text does not retrieve any cited chunk, is rejected with `not_answerable_from_chunks`.
3. A batch with good and bad items stores the good ones and reports the bad ones.
4. A `code` kind is refused; a question on a parent topic is refused.
5. Removing a material removes each question whose only citations were in it and keeps a question that still cites another material.
5a. A version-1 `store.db` with a tree opens, gains the empty question tables, and keeps its materials, chunks and tree unchanged; the map page still shows it.
5b. A `submit_tree` that drops or subdivides a topic holding questions is rejected with `topic_has_questions` and its count, and nothing is written; after `remove_questions` the same tree is accepted.
6. The bank survives export and import; import refuses a bundle with dangling question references or a malformed question (for example a `single` with no correct option); `progress.db` is not in the bundle.

**Slice 2: the test page, instant grading, mastery.** Draw, attempt save, result, history, mastery on the map, `progress`.

7. A drawn test contains no key and no reference answer anywhere in the response.
8. Submitting grades single and multiple answers correctly, including a multiple answer that is right only when the set matches exactly, and writes one attempt in one transaction.
9. A `POST` with a foreign or missing `Origin`, a non-JSON body, an unknown question id or an option from another question is refused and writes nothing.
10. The draw prefers unanswered, then last-wrong questions, and spreads across subtopics.
11. Mastery follows section 10 at its boundaries: 9 graded answers, 10 with 8 correct, 10 with 9 correct, a parent with one subtopic not mastered.
12. The page still loads no model and writes no rows to `store.db`; a stem, option or code block containing `<script>` renders as text.
13. Human review: Paul sits a 10-question test on apd in a desktop and a phone-width window and reports it reads well.

**Slice 3: open answers and host review.** `review_answers`, the awaiting state, the skill flows.

14. An open answer is saved as awaiting review, is excluded from the score and from mastery, and the result is marked provisional.
15. A recorded verdict appears on the result with its comment as text, the score stops being provisional, and mastery now counts the answer.
16. A second verdict for the same answer and an unknown answer id are refused, and a call with one bad verdict records none.
17. End to end on Claude Code: "write me ten questions on OpenMP reductions", then a test on the page, then "grade my test", with no other instruction.

## 13. Left to the builder

- Exact column types and index choices.
- Wording of tool descriptions, tool errors and page text, within the project's conventions.
- Whether `review_answers` stays one tool or becomes two.
- The page's visual detail, following the existing stylesheet. The mockup that was approved is in `.superpowers/brainstorm/` (not tracked).
- Whether the draw's round-robin needs a random tie-break.
