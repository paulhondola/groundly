# Detailed skills: one skill per flow, plus a `topic` read tool

Paul, 2026-11-18. Project 1 of three agreed in the brainstorm: (1) this, (2) an in-depth topic summary written by a page run and shown on the page, (3) repository subjects with a code graph. Projects 2 and 3 get their own designs.

## Why

The one packaged skill (`groundly/skill/SKILL.md`, about 750 words) holds three procedures: map a course, write questions, grade a test. Each gets five to seven lines, and they are thinnest exactly where quality is decided. "Write questions" says nothing about wrong options, difficulty, or covering all of a topic's pages; "Grade a test" says nothing about what counts as correct. The gates cannot make up for it: `submit_questions` shows a question is well formed and about its cited chunks, never that its key is right, and `review_answers` stores whatever verdict comes.

Paul wants room to write each procedure in detail, on its own. Splitting also gives project 2's summary skill a slot, and lets a page run (decision 46) carry only its own flow.

The detailed question procedure needs two things the tools do not hand over today: a topic's own source pages (`outline` gives `source_count`, `progress` gives titles) and the questions already in its bank. Hence one read tool.

## Goals

1. Three packaged skills, each self-contained: `groundly-map`, `groundly-questions`, `groundly-grade`.
2. Each written in detail, meeting the content requirements below.
3. A read-only MCP tool `topic(subject, topic_id)` returning a topic's title, summary, place in the tree, source pages and existing questions.
4. A page run loads only its flow's skill; a write run may also call `topic`.
5. `groundly skill` (status), `install` and `uninstall` handle the three skills and retire the old `groundly/` folder.

## Non-goals

- The in-depth summary skill and feature (project 2) and repository subjects (project 3).
- Codex or Claude Desktop skill setup.
- Changing any gate: `submit_tree`, `submit_questions`, `review_answers` and `remove_questions` check exactly what they check today.
- New page buttons. Mapping stays in chat (decision 46 rejected a Map button).
- Skill support files (examples, references) beside a `SKILL.md`: a page run cannot read them.

## Anti-goals (a failure even if it works)

- Any page run gaining a tool outside Groundly's MCP server: no Read, Grep, Glob, Bash or web. An injected instruction in imported course text could then read a file off the disk and store it in a question stem, which the next export ships.
- Any skill or tool description calling a summary, an answer key or a verdict verified.
- A skill that drops the paragraph saying course text, stored titles, summaries, questions, reference answers and student answers are data, never instructions.
- The server `instructions` ranking one tool above another (decision 31).
- Layer-3 text (a title, summary, stem or chunk) entering a page run's prompt.
- An edited or older installed skill silently overwritten, silently trusted, or written through a symlink.

## Constraints

- **No LLM, no key** (decision 37): skills are text the host follows; `topic` is a store read with no model load.
- **Page runs** (decision 46): `--tools ""`, `--strict-mcp-config`, a per-flow `--allowedTools`, `--append-system-prompt-file` with one file, a fixed prompt holding the subject name and an integer topic id only, init event checked fail-closed. Adding `topic` to the write run's tools is a decision change.
- **Tool surface** is pinned by `tests/mcp/test_mcp_server.py::test_the_tool_surface_is_exactly_these_ten_entries`; adding `topic` makes it eleven, deliberately.
- **Tool descriptions are UX** (conventions): lead with when to call; say stored text is untrusted data.
- **Trust layers**: each `SKILL.md` is layer 1 in a page run; everything `topic` returns is layer 3, including titles and stems an imported bundle may have written.
- A decision change updates the docs in the same change set (`/decision`).

## Approaches considered

- **A. Separate self-contained skills** (chosen by Paul). One folder per flow under `~/.claude/skills/`, each with its own trigger description, setup checks and data paragraph. A page run appends exactly its flow's file. Cost: the setup checks and the data paragraph repeat in each file; a test pins the paragraph in every one.
- **B. One `groundly` skill as a router plus a file per flow.** One install folder and one description. Rejected: page runs have no tool to open a flow file, so each flow file would have to be appended directly and stand alone anyway — A in one folder, behind one broader trigger.
- **C. One longer `SKILL.md`.** No code change. Rejected: every page run carries every flow, and the file grows again with project 2, which works against detailing each flow.

## Design

### Packaged layout and names

```
groundly/skills/
  groundly-map/SKILL.md
  groundly-questions/SKILL.md
  groundly-grade/SKILL.md
```

installed as `~/.claude/skills/<name>/SKILL.md`. The old `groundly/skill/SKILL.md` is removed from the package. (Claude's call: the package mirrors the install layout so one loop serves install, status and page runs.)

Each skill's frontmatter `description` carries its own triggers, taken from today's combined description:

| Skill | Triggers |
|---|---|
| `groundly-map` | map a course, build a topic tree or roadmap of a subject, update the map after adding materials |
| `groundly-questions` | write questions on a topic, "test me", "quiz me" |
| `groundly-grade` | "grade my test", review the written answers of a test |

### What every skill carries

- **Connection check**: if Groundly's tools are missing, give `claude mcp add --scope user groundly -- groundly mcp`, say to restart Claude Code, stop.
- **Subject check**: `list_subjects`; an unlisted subject or one with new materials needs `groundly index` first (map), or a map first (questions: a subject with no tree is pointed to `groundly-map` by name).
- **Data paragraph**: the text in the anti-goals, worded for the flow, containing the phrase "data, never instructions".
- **Hand-over**: what to tell the student, never calling a summary, key or verdict verified.

In a page run the connection and subject checks are moot (the run is isolated and given a valid subject and topic id); they must not make the run stop or ask anything.

### Content requirements per skill

Today's steps carry over; these are the additions. Proposed by Claude in the brainstorm; Paul raised no objection.

**`groundly-map`** (chat only)
- Size each subtopic so the course can carry 10 distinct questions on it: that is what Write questions fills a subtopic to and what mastery counts over.
- Name topics in the course's own terms and language; never invent acronyms.
- Write each short summary (at most 500 characters) with the key terms the slides use: the gate searches on `title + summary` to pick the node's sources. Never pad a summary to pass.
- A long outline is read in parts, with subagents where available.
- Re-map: send back every kept `id`; when `submit_tree` rejects with `topic_has_questions`, explain to the student which topic holds questions and offer `remove_questions` (with confirmation) or keeping the topic.
- Real course order, not filename order ("Curs 10" sorts before "Curs 2").

**`groundly-questions`** (chat and the page's write run)
- Start with `topic`. A source with a page (`#page=N` in its uri) is read in full with `get_page`, the uri as given, never URL-encoded, each page once. A source with no page (plain text, notebooks, most Markdown and DOCX) is never opened whole, since its uri returns the entire material: work from the chunk text `topic` returns and `search` its heading for what surrounds it. Then `search` around the topic for what the sources miss. A topic with no sources ("sources removed") is written from `search` on its title and summary.
- In a run that fills several subtopics, finish one subtopic (submitted) before starting the next, so a run cut off by its time limit leaves whole subtopics done.
- Read the existing stems from `topic` and write nothing that repeats or near-repeats one.
- Spread questions across the topic's pages; one idea per question; a stem that makes sense on its own.
- Mix recall, understanding and application, and the kinds the material supports (`single`, `multiple`, code-reading, `open`).
- Wrong options come from confusions the course itself names or invites; similar in length and form to the right one; no "all of the above" or "none of the above".
- Write in the course's language and notation.
- An `open` question's reference answer lists the points a correct answer must contain: it is what grading judges against.
- Never a question the cited chunks do not answer, and never one answered by writing code.
- Rejections: rewrite from `reason` and `detail`, resubmit only those; drop what the course does not support.

**`groundly-grade`** (chat and the page's grade run)
- Correct means the answer covers every point the reference answer requires; wording, language and spelling do not count against it.
- A half-right answer is decided by whether what is missing is a required point.
- A blank, off-topic or copied-question answer is incorrect.
- The comment (one or two sentences, at most 1000 characters) names what was missing or wrong and the page to read.
- One standard across the whole batch; judge only against the reference answer and the cited chunks, never general knowledge.
- Loop while `remaining` is above 0.

Length: each skill as long as its procedure needs, written as instructions, no rationale essays: a page run carries its whole skill on every click. (Claude's call; a soft target of about 1500 words per skill.)

### The `topic` tool

`topic(subject: str, topic_id: int) -> dict`, `readOnlyHint`. Reply:

```json
{
  "id": 42,
  "title": "…",
  "summary": "…",
  "parent": {"id": 7, "title": "…"},
  "subtopics": [{"id": 43, "title": "…", "questions": 3}],
  "sources": [{"chunk_id": 900, "uri": "groundly://apd/Curs 3.pdf#page=12",
               "filename": "Curs 3.pdf", "page": 12, "heading_path": "…", "text": "…"}],
  "questions": [{"id": 101, "kind": "single", "stem": "…"}]
}
```

- `parent` is null for a big topic; `subtopics` is empty for a leaf; `questions` is empty for a big topic (it takes none).
- Each source carries its own chunk's verbatim text (a node has at most 5), so a source with no page can be read without opening its whole material; `get_page` opens a paged source's full page. No options and no answer keys: the writer has no need for stored keys. (Claude's call, settled after the builder check: page-less sources made "uris only" unworkable, and five chunks keep the reply small.)
- `sources` in rank order; empty when the node's chunks were removed. Each `uri` is the one `search` would give for that chunk (`#page=N` only when the chunk has a page).
- An unknown `topic_id` (or a non-integer) raises a `ToolError` that says to call `outline`.
- Description leads with when to call ("before you write questions on a topic, or to see what a topic covers"), says titles, summaries and stems are untrusted course data, never instructions, and ranks no tool.
- Built from `SubjectStore.tree()` and `question_stems`-style reads; the store helper's shape is the builder's.

### Page runs

- `groundly/web/runs.py`: the appended file depends on the flow: `write` → `groundly-questions/SKILL.md`, `grade` → `groundly-grade/SKILL.md`.
- `WRITE_TOOLS` gains `mcp__groundly__topic`. `GRADE_TOOLS` is unchanged.
- The prompt templates name the skill instead of a section heading ("Follow the groundly-questions skill for subject …, topic id …"); still a fixed template with the subject name and an integer only. Exact wording is the builder's.
- The init check stays as it is (any non-`mcp__groundly__` tool or other server fails closed).
- `RUN_TIMEOUT_S` stays 20 minutes. The detailed procedure does more reads per subtopic, so a big-topic run may now hit the limit; questions stored before it stay, and another click fills what is left. Criterion 7 times a big-topic run on apd; the limit is raised in a follow-up only if that run times out. (Claude's call: no run has been timed on real `claude` yet, so a new limit would be a guess.)

### Install, status, uninstall

- **Status** (`groundly skill`): one line per skill — not installed / installed / out of date — plus a line for the old `groundly/` skill if it is still there ("old combined skill installed — run `groundly skill install` to replace it").
- **Install**: each of the three under today's per-file rules (byte compare; "already installed" when identical; prompt before replacing a different copy unless `-y`; refuse a symlinked `SKILL.md`). Then retire the old `~/.claude/skills/groundly/SKILL.md` if present: refuse a symlink, prompt before removing unless `-y`, remove the folder only if empty. Left in place, it would trigger beside the new skills.
- **A "no" to one prompt skips only that path** and install carries on with the rest, exiting 0. The output names each skipped path; declining the old skill's removal adds that it will keep triggering beside the new skills until removed. A refused symlink still fails the command, as today, before anything is written. (Claude's call, settled after the builder check: all-or-nothing would leave a student who edited one skill only `-y`, which overwrites the edit.)
- **Uninstall**: removes the three and the old one if present, one prompt for all unless `-y`, keeping other files in each folder as today.
- The install message ends as today: the connect command, then "say: map my course".

### Docs (one `/decision` change)

- `docs/groundly-spec.md`: a new decision recording three skills and the `topic` tool; amend decision 41 (one skill → three) and decision 46 (each run appends its flow's skill; the write run's tools gain `topic`).
- `.claude/rules/conventions.md`: the pinned surface becomes eleven entries. `.claude/rules/architecture.md` and `grounding-and-privacy.md`: "the packaged `SKILL.md`" → the flow's packaged skill.
- `docs/architecture/agents.md`, `docs/infrastructure/security.md` (page-run tool lists), `docs/guides/mcp-hosts.md` (install copies three skills), `docs/use-cases/student-modes.md` (setup and the skill criteria), README if it names the skill.

## Acceptance criteria

1. `groundly skill install` on a home with the old skill installs the three and, after a prompt (or `-y`), removes the old one; status then shows three "installed" lines.
2. Install, status and uninstall keep today's guarantees for each file: byte compare, prompt before replacing, `-y`, symlinks refused and never followed, other files kept.
3. Every packaged skill contains the data paragraph and never the word "verified" (as today's test pins: the hand-over says what a check shows instead); each skill's description holds its triggers (tests replace `test_the_skill_covers_writing_questions_and_grading_a_test`).
4. The MCP surface test lists eleven entries including `topic`.
5. `topic` on a leaf returns its sources as uris in rank order and its stems; on a big topic its subtopics with question counts and no questions; on a node whose chunks are gone, empty `sources`; on an unknown id, a `ToolError` naming `outline`. Each source carries its chunk's text and a uri without `#page` when the chunk has no page. It returns no options or answer keys and loads no model.
6. A write run's argv appends `groundly-questions/SKILL.md` and allows `topic`; a grade run's appends `groundly-grade/SKILL.md` and its tools are unchanged.
7. **End to end on Claude Code** (manual, Paul): "map my course" triggers `groundly-map` and reaches an accepted tree; "write questions on <topic>" triggers `groundly-questions` and calls `topic` first; a page **Write questions** run calls `topic`, reads the source pages and stores questions; a big-topic **Write questions** run on apd is timed and recorded with the criteria; **Grade now** reviews every awaiting answer.
8. **Human review** (Paul): on apd, ten questions written by the new skill for one subtopic cover more than one source page, contain no near-duplicate of an existing stem, and their wrong options are plausible to a student of the course.

## Left to the builder

- The skills' exact wording, within the content requirements.
- The store helper behind `topic` and its query shape.
- The page-run prompt wording.
- Test names and structure.
