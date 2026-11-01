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
7. **Read the map.** `groundly serve [--port N]` (default 8765) prints the URL, opens the browser and serves every subject from one process on 127.0.0.1. A node opens a side panel with its title, "Not started", its summary and its sources (`file.pdf · p.17`). A PDF source opens the stored file at that page; any other format shows the cited chunk text. Re-running the flow is the same: the host sends back the ids it keeps, so topic ids survive.

**Alternate / error flows**

- **A1 — No tree yet:** the page shows the sentence to say to the host and notes that the skill must be installed.
- **A2 — A cited material was removed:** its citations go with it, the node stays and shows "sources removed — ask your agent to update the map". The next accepted submit restores or removes it.
- **A3 — Subject indexed before the tree tables:** search still works. Tree calls and the page report SQLite's own "no such table" message; no guard or migration is added (decision 41), and the fix is `groundly drop` then re-index once.
- **A4 — Imported bundle:** the tree arrives with `store.db`. Import refuses a bundle with no tree tables (exported by an older Groundly) or with dangling references or a tree deeper than two levels. Titles, summaries and chunk text are layer-3 data.

**Out of scope for this release:** progress (every node reads "Not started"; no progress tables, nothing written to `progress.db`), a generate-quiz button and deep links into the host (quizzes, sub-project 3), Codex and Claude Desktop setup, and any work at index time. The gate shows a topic is in the course; it never shows a summary is true, and nothing may call a summary verified.

**Acceptance criteria**

1. `outline` returns materials, pages, headings and the current tree, loads no model, and stays under 15,000 tokens on apd.
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

Criteria 12–13, the token budget in 1 and the end-to-end half of 11 are manual and stay open until recorded. The rest have tests; criterion 10 is checked as "the page has no HTML-inserting sink", not by rendering a hostile bundle.

## Planned

Each gets its own spec, with acceptance criteria written here, before implementation.

| Use case | Sub-project | Note |
|---|---|---|
| **Verified quizzes + mastery** | 3 | Multiple-choice and open questions per topic, submitted through the verifier gate; answer-key and distractor checks are structural (no provider). Answers are recorded in `progress.db`, never exported. A topic is mastered at ≥90% correct over its last 10 answers (at least 10 answered); a parent is mastered when all its subtopics are. Code questions are refused at the door. |

## Ideas, not planned

- **Coding challenges** — code questions checked by running a reference solution in a subprocess runner (timeout, tempdir, argv exec). Most tractable for Python courses. Until a runner exists, no code question may enter `store.db`.
