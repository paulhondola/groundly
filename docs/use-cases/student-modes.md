# Use Cases: Study Modes

Detail for [`groundly-spec.md`](../groundly-spec.md) §3. Actor: a **host agent** (MCP tools) conversing with the student. A learning session stays in the host plus Groundly's local page (decision 38).

## Topic tree (sub-project 2)

**Actor:** host agent (`outline`, `submit_tree`) building the map; student (`groundly serve`) reading it. Decision 41.
**Preconditions:** the subject is indexed under the current schema; for building, the host is Claude Code with the Groundly server connected and the skill installed.

A roadmap-style map of a subject: big topics in course order, subtopics under each (exactly two levels), and for every node a short summary and the slides behind it. It lives in `store.db`, so it travels with bundles. No graph, no extra model (decision 37).

**Main flow**

1. **Set up, once.** `claude mcp add groundly -- groundly mcp` connects the server; `groundly skill install` copies the packaged skill to `~/.claude/skills/groundly/`. Connecting stays a separate step because `skill install` never edits another tool's config. Bare `groundly skill` shows not installed / installed / out of date; `skill install` and `skill uninstall` prompt before replacing or removing unless `-y/--yes`.
2. **Trigger.** The student asks the host to map the course. If the tools are missing, the skill gives the connect command and says to restart the host; if the subject is not indexed, it runs `groundly index`.
3. **Read.** The host calls `outline(subject)`: each indexed material with its page count, its pages' headings and how many of its chunks the current tree cites, plus the current tree with topic ids. No chunk text and no chunk ids.
4. **Think.** The host merges overlapping lectures, drops noise, orders big topics by course order and writes a summary (at most 500 characters) per node. Groundly does not filter headings.
5. **Submit.** The host calls `submit_tree(subject, tree)` with `[{id?, title, summary, subtopics: [{id?, title, summary}]}]`; list order is course order. The host cites nothing: Groundly retrieves on `title + "\n" + summary` with the reranker always on, and the hits at or above `TOPIC_RELEVANCE_CUTOFF` (1.0, up to 5) become the node's sources. The whole tree is accepted or rejected, in one transaction.
6. **Fix.** A rejection lists every failing node at once with a machine-readable reason (`empty_tree`, `invalid_text`, `unknown_topic_id`, `duplicate_topic_id`, `too_deep`, `too_large`, `below_relevance_cutoff`). A `below_relevance_cutoff` entry carries the top score and the nearest hit's filename, page and heading, so the host rewords in the course's terms. Structural failures are reported without running the search.
7. **Read the map.** `groundly serve [--port N]` (default 8765) prints the URL, opens the browser and serves every subject from one process on 127.0.0.1. A node opens a side panel (a sheet over the tree on a phone) with its title, "Not started", its summary and its sources (`file.pdf · p.17`). A PDF source whose file is stored opens it at that page, once per page; any other source, including a PDF exported without its materials, shows the cited chunk text. Re-running the flow is the same: the host sends back the ids it keeps, so topic ids survive.

**Alternate / error flows**

- **A1 — No tree yet:** the page shows the sentence to say to the host and notes that the skill must be installed.
- **A2 — A cited material was removed:** its citations go with it, the node stays and shows "sources removed — ask your agent to update the map". The next accepted submit restores or removes it.
- **A3 — Subject indexed before the tree tables:** search still works. Tree calls and the page report SQLite's own "no such table" message; no guard or migration is added (decision 41), and the fix is `groundly drop` then re-index once.
- **A4 — Imported bundle:** the tree arrives with `store.db`. Import refuses a bundle with no tree tables (exported by an older Groundly) or with dangling references or a tree deeper than two levels. Titles, summaries and chunk text are layer-3 data.

**Out of scope for this release:** progress (every node reads "Not started"; no progress tables, nothing written to `progress.db`), a generate-quiz button and deep links into the host (quizzes, sub-project 3), Codex and Claude Desktop setup, and any work at index time. The gate shows a topic is in the course; it never shows a summary is true, and nothing may call a summary verified.

**Acceptance criteria**

1. `outline` returns materials, pages, headings and the current tree, loads no model, and its reply reaches the host whole on a subject the size of apd.
2. A tree with a real topic is accepted, each node has 1–5 sources, and every source resolves to a document and page.
3. An off-topic node (e.g. "French Revolution") rejects the whole tree with `below_relevance_cutoff`, its score and nearest heading, and nothing is written.
4. Several bad nodes are all reported in one rejection.
5. Unknown and duplicate ids, a title over 200 or a summary over 500 characters, more than 1000 topics, and more than two levels are all refused.
6. Re-submitting with ids keeps those ids; a new node gets a new id; a deleted id is never reused.
7. Removing a cited material keeps the node, flagged, and the next submit restores its sources.
8. `store.db` carries the tree through export and import, `progress.db` is not in the bundle, and import refuses a bundle whose citations do not resolve.
9. `serve` binds 127.0.0.1, rejects a foreign Host header, loads no model and writes no rows to `store.db`. Each page path works: subject list, tree, empty state, flagged node, PDF at a page, chunk text for a non-PDF.
10. A title containing `<script>` in an imported bundle renders as text.
11. `groundly skill install`, `uninstall` and the bare status all work, and a replaced copy prompts unless `-y`. **End to end on Claude Code:** with the server connected and the skill installed, "map my course" alone leads to an accepted tree. With the server not connected, the skill gives the connect command.
12. **Human review:** on a fully re-indexed apd, the skill builds a tree Paul recognises as the course against the issue's seven big nodes (Intro, POSIX Threads, Synchronization, Performance, OpenMP, MPI, Sockets & Distributed). The review is recorded with the criteria.
13. **Cutoff check before release:** Claude Code-written trees on apd and `passc` pass without wrongly rejected nodes piling up. Change the constant if they do.

**Recorded 2026-11-01 (Paul, Claude Code):** apd (14 big topics, 66 subtopics) and `passc` (9 big topics, 38 subtopics) were both mapped and accepted, every node with sources, and Paul reviewed the maps and reports they look right. The apd map goes beyond the seven big nodes where the course does: dense matrix, OpenMP tasking, Java threads, Raft, GPU. One apd node was rejected on the first submit and passed after rewording, and no pile-up of wrong rejections was reported, so the cutoff stays at 1.0. With the server not connected, the skill gave the connect command. Submit duration was not timed.

Criteria 1, 12, 13 and the end-to-end half of 11 are manual and recorded above. The rest have tests; criterion 10 is checked as "the page has no HTML-inserting sink", not by rendering a hostile bundle.

## Tests on the local page (sub-project 3, in progress)

**Actor:** host agent (`submit_questions`, `remove_questions`, `review_answers`, `progress`) writing and reviewing; student (`groundly serve`) taking the test. Decision 44; full design in [`2026-11-16-tests-on-the-page-design.md`](../superpowers/specs/2026-11-16-tests-on-the-page-design.md).
**Preconditions:** the subject has a topic tree.

**Main flow**

1. **Write.** The student asks the host for questions on a topic. The host searches the topic and writes single-answer, multiple-answer, code-reading (a choice question with a code block) or open questions, each citing 1 to 5 chunk ids.
2. **Gate.** `submit_questions(subject, topic_id, questions)` checks each question on its own: structure first, then that the cited ids resolve and that re-retrieving the question's own text finds one of them. Passing questions are stored; the rest come back with a machine-readable reason. Only a topic without subtopics takes questions.
3. **Take.** On the page the student picks a subtopic, a big topic or the whole course and a length of 5, 10 or 20. Groundly draws from the bank, unanswered and last-wrong questions first, and shows one question per screen with no answer key in the browser.
4. **Submit.** One request grades the choice questions and saves the attempt to `progress.db`. The result shows the score, each right answer and its source page. An open answer waits for review.
5. **Review.** The student says "grade my test". The host reads the answers awaiting review with `review_answers`, judges each against its reference answer and cited chunks, and records a verdict and a comment that the result then shows.
6. **Mastery.** A topic is mastered at 9 or more correct of its last 10 graded answers, with at least 10. A big topic is mastered when all its subtopics are. The map shows each node's state.

**Alternate / error flows**

- **A1 — No questions in scope:** the page shows the sentence to say to the host.
- **A2 — A cited material is removed:** a question left without citations is deleted; one that still cites another material stays.
- **A3 — A re-map would drop or subdivide a topic holding questions:** `submit_tree` rejects it with `topic_has_questions`; the host keeps the topic or removes its questions first.
- **A4 — A wrong question:** the result shows each question's number; the host removes it with `remove_questions`. Recorded answers stay.
- **A5 — A question removed while an open answer awaits review:** the answer becomes "cannot be reviewed" and counts nowhere.
- **A6 — Subject indexed before the question tables:** opening its `store.db` adds the empty tables in place.

**Out of scope:** live coding (code questions the student answers by writing code are refused at the door), a quiz asked in the chat, fixed tests written by the host, timers, partial credit. The gate shows a question is well formed and about its cited chunks; it never shows an answer key is right, and nothing may call a key verified.

**Acceptance criteria** (none met yet)

1. A well-formed question of each kind citing real chunks is stored; each structural fault is rejected with its reason and nothing is stored for that item.
2. A question citing an unknown chunk, or whose text does not retrieve any cited chunk, is rejected with `not_answerable_from_chunks`.
3. A batch with good and bad items stores the good ones and reports the bad ones.
4. A `code` kind is refused; a question on a parent topic is refused.
5. Removing a material removes each question whose only citations were in it and keeps a question that still cites another material.
5a. A version-1 `store.db` with a tree opens, gains the empty question tables, and keeps its materials, chunks and tree unchanged; the map page still shows it.
5b. A `submit_tree` that drops or subdivides a topic holding questions is rejected with `topic_has_questions` and its count, and nothing is written; after `remove_questions` the same tree is accepted.
6. The bank survives export and import; import refuses a bundle with dangling question references or a malformed question; `progress.db` is not in the bundle.
7. A drawn test contains no key and no reference answer anywhere in the response.
8. Submitting grades single and multiple answers correctly, including a multiple answer that is right only when the set matches exactly, and writes one attempt in one transaction.
9. A `POST` with a foreign or missing `Origin`, a non-JSON body, an unknown question id or an option from another question is refused and writes nothing.
10. The draw prefers unanswered, then last-wrong questions, and spreads across subtopics.
11. Mastery holds at its boundaries: 9 graded answers, 10 with 8 correct, 10 with 9 correct, a parent with one subtopic not mastered.
12. The page still loads no model and writes no rows to `store.db`; a stem, option or code block containing `<script>` renders as text.
13. **Human review:** Paul sits a 10-question test on apd in a desktop and a phone-width window and reports it reads well.
14. An open answer is saved as awaiting review, is excluded from the score and from mastery, and the result is marked provisional.
15. A recorded verdict appears on the result with its comment as text, the score stops being provisional, and mastery now counts the answer.
16. A second verdict for the same answer and an unknown answer id are refused, and a call with one bad verdict records none.
17. **End to end on Claude Code:** "write me ten questions on OpenMP reductions", then a test on the page, then "grade my test", with no other instruction.

## Ideas, not planned

- **Coding challenges** — code questions checked by running a reference solution in a subprocess runner (timeout, tempdir, argv exec). Most tractable for Python courses. Until a runner exists, no code question may enter `store.db`.
