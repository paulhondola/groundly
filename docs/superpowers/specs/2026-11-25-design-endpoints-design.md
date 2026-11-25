# Endpoints for the "Quiet dark + one orb" page design

Paul, 2026-11-25. Branch `feat/design-endpoints`. Proposes decision 53.

## Intent

The page is being redesigned ("Quiet dark + one orb", design canvas artifact `1b476cbd`). Its
screens show things today's API cannot drive: a mastery ring and "last test" on every subject card,
an answer checked one question at a time, an MCP host card with recent activity, indexing progress,
a particle orb that shows what the host is doing, and a Share-a-course export. This change builds the
**endpoints only**, so the reskin that follows is a pure UI pass against a finished API.

## Goals

1. **Check answer:** the test page can reveal one choice question's key and cited sources
   before the test is submitted, and the answer still counts only when the test is submitted.
2. **Subject cards:** `GET /api/subjects` carries the counts behind the mastery ring, the topic and
   leaf counts, and the last test time.
3. **Activity log:** every Groundly process (stdio `groundly mcp`, `/mcp` inside `groundly start`,
   `groundly index`) records what it is doing to one private local file; `GET /api/activity` turns
   it into the host card, the orb state, recent activity and indexing progress.
4. **Export:** the page can download a subject's `.groundly` bundle, from this computer or a paired
   phone.

## Non-goals

- Any UI. The Svelte reskin is the next branch.
- Splitting the bundle (topic map and question bank as separate options). `store.db` ships whole; the
  interchange format does not change.
- A new MCP tool. The tool surface stays exactly eleven entries.
- Live push of activity. The page polls.

## Anti-goals (a failure even if it works)

- **Progress leaves the machine.** Export never reads `progress.db`, `remote.db` or `activity.db`. The
  design's "My mastery history" checkbox is not built.
- **Course text in the activity log.** It holds subject names, tool names, counts, file names and host
  names only: never chunk text, summaries, questions or answers.
- **Activity logging breaks real work.** A failed log write never fails a tool call, a gate or an
  index run.
- **A check and a submit disagree.** Both grade through the same function.
- **An answer key is called verified** in a response field, a doc or a description.

## Constraints

- `.claude/rules/*`: layering (`web/`, `mcp/`, `cli/` are clients; the activity store is a
  foundation in `core/`), SQLite WAL + busy_timeout on every connection, `PRAGMA user_version`,
  the page writes no rows to `store.db`, write routes require the page's Origin and a JSON body,
  Tailscale socket gating (`groundly/web/remote.py`), private files created only on demand.
- Decision 44: the page's one write to `progress.db` is a submitted attempt. The check route keeps
  that true by writing nothing.
- Decision 52: on the Tailscale socket every non-shell route needs a paired session cookie; `/mcp`
  and `/api/remote*` stay 404 there.
- Tests use SQLite files and stub embedders, no Node.
- `web/openapi.json` and `web/src/lib/api/schema.d.ts` are regenerated from the FastAPI app.

## Approaches considered

- **A. One endpoint branch, four commits, UI afterwards (chosen).** Check answer, subject fields,
  export, activity log, in that order; each commit carries its tests and doc changes; OpenAPI and
  TypeScript types regenerated once at the end. The riskiest part (activity, touching MCP and
  indexing) comes last so the three small commits review easily.
- **B. Four PRs.** Easier review each, but four regenerations of the generated schema that conflict,
  and decision 53 split across PRs.
- **C. Vertical slices with their UI.** Contradicts "endpoints first" and fragments the reskin.
- **Activity over the existing `/api/runs/stream` SSE (rejected).** That stream serves live page
  runs; host activity changes every few seconds at most, so polling every 3 s is enough and keeps
  the stream's job narrow.

For hosts (goal 3), two alternatives were also rejected: tracking sessions in memory on `start`'s
`/mcp` only (the documented stdio setup would read "No host connected" while a host works), and
dropping the host card (the orb could then react only to page runs).

## Decided design

### 1. Check answer (commit 1)

```
POST /api/subjects/{subject}/questions/{id}/check
body:  { "selected": [option ids] }
200:   { "correct": bool,
         "key": [option ids],
         "sources": [SourceItem] }
400 invalid_text       open question; an option that is not the question's own; >1 for single
400 invalid_body       body does not match
404 unknown_question   no such question (removed since the draw)
404 unknown subject
403/415                not the page's Origin / not JSON (same dependency as submit)
```

- A new `quiz.check_answer(store, question_id, selected)` in `groundly/agents/quiz.py` grades through
  the existing `_grade`, so check and submit cannot disagree. It returns the key (`correct` options)
  and the question's sources as `SourceItem` (verbatim chunk text included), with `pdf` set the same
  way `GET /tree` sets it.
- **No explanation field** (Paul, settled in review): choice questions store no
  `reference_answer` (the gate sets it for open questions only and import rejects it on a choice
  question). The feedback box shows the key plus the cited page and its verbatim passage instead,
  so what the student reads is their own course material, not host-written text.
- It writes nothing. The page's lock is client-side: the checked selection is stored with the
  in-progress test the page already persists (`test-session.ts`), so a reload keeps it locked, and
  the submitted attempt uses it.
  `ponytail:` the lock lives in the browser; a hand-made API call could check and then submit a
  different choice. The page has one user, the student; a server-side lock would need per-test
  state and a write before submit.
- *Builder's call (settled in review):* the lock ships with the Check UI in the reskin branch, not
  here; until then the route has no caller. The key it reveals is the same key the result page
  shows after submit. Decision 53's text says the lock is client-side.
- *Builder's call:* `_quiz` maps quiz errors to 400 today; `unknown_question` from this route maps to
  404 as listed above.
- Open questions have no check; they are graded by the host later, as today.
- Response wording never says "verified".

### 2. Subject card fields (commit 2)

`SubjectSummary` (from `GET /api/subjects`) gains four optional fields; the route stays.

```
SubjectSummary {
  subject, title, has_tree, error?   (unchanged)
  topics?:          int   # top-level nodes
  leaves?:          int   # subtopics, plus topics that have none
  leaves_mastered?: int
  last_test_at?:    str   # newest attempt's submitted_at, same format as AttemptSummary
}
```

- `leaves` and `leaves_mastered` come from `quiz.mastery()`, so a card agrees with its topic map.
  The page computes the ring's ratio.
- `topics`, `leaves`, `leaves_mastered` are present when the subject has a tree; `last_test_at` when
  it has at least one attempt.
- If the subject has no `progress.db`, the listing does not create one: `leaves_mastered` is `0`
  and `last_test_at` is omitted.
- A store error still sets `error` and omits the new fields.
- `ponytail:` the listing reads every subject's tree and answers on each call; fine for tens of
  subjects, cache per `store.db`/`progress.db` mtime if it reaches hundreds.

### 3. Export (commit 3)

```
GET /api/subjects/{subject}/export/info
  200 { "filename": "apd.groundly", "store_bytes": int, "materials_bytes": int }

GET /api/subjects/{subject}/export?materials=true|false   (default true)
  200 application/zip
      Content-Disposition: attachment; filename="apd.groundly"
  404 unknown subject
  409 manifest_invalid   the subject's manifest fails the same check the CLI runs
```

- Reuses `groundly.core.bundle.export_subject(subject, out, include_materials=...)`, the CLI's code
  path. The bundle is built into a temporary file (system temp dir, in the thread pool), streamed
  with `FileResponse`, and deleted by a background task after the response, or on failure.
- Sizes in `info` are on-disk bytes of `store.db` and the materials directory; the page shows the
  total as "about N MB" since zip compression changes it.
- Plain GETs, so a link or `<a download>` triggers the browser download; they write nothing, so no
  Origin/JSON dependency. Host allowlisting still applies.
- **Paired phones may export** (Paul): on the Tailscale socket both routes are gated by the paired
  session cookie like every other read route, not 404'd.
- Export code reads `store.db`, the manifest and `materials/` only.
- `ponytail:` the whole bundle is built before the first byte is sent; a large materials folder
  means a wait with no progress. Stream the zip as it is written if that hurts.

### 4. Activity log (commit 4)

**File.** `~/.groundly/activity.db` (under `GROUNDLY_HOME`), mode `0600`, SQLite WAL + busy_timeout,
`PRAGMA user_version = 1`, created on first write, never exported, never read by export or import.
A new foundation module `groundly/core/activity.py` owns it (writer and reader).

```
CREATE TABLE events (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  at      REAL    NOT NULL,              -- unix seconds
  pid     INTEGER NOT NULL,
  source  TEXT    NOT NULL CHECK (source IN ('mcp_stdio','mcp_http','index')),
  session TEXT,                          -- MCP session id for mcp_http, else NULL
  client  TEXT,                          -- host's self-reported name, e.g. 'claude-code'
  subject TEXT,
  event   TEXT    NOT NULL,
  detail  TEXT    NOT NULL DEFAULT '{}'  -- small JSON: counts, tool name, file name
);
CREATE INDEX idx_events_at ON events(at);
```

**Events and who writes them.**

| Writer | Event | detail |
|---|---|---|
| MCP (stdio and `/mcp`) | `session_start` | `{}` (client from the MCP initialize's clientInfo name) |
| MCP | `tool_call` | `{"tool": name}`, subject when the tool takes one; written when the call starts |
| MCP | `tool_end` | `{"tool": name}`; written when the call returns or raises |
| MCP, after the gate accepts | `tree_accepted` | `{"topics": n, "subtopics": n}` |
| MCP, after the gate accepts | `questions_accepted` | `{"topic_id": id, "count": n}` |
| MCP, after verdicts are stored | `answers_reviewed` | `{"count": n}` |
| MCP | `session_end` | clean shutdown only |
| `groundly index` | `index_start` | `{"files": n}` |
| `groundly index` | `index_file` | `{"n": i, "of": n, "file": name, "status": s}` |
| `groundly index` | `index_done` / `index_failed` | `{"indexed": n, "failed": n}` / `{"reason": s}` |

- MCP writes live in `groundly/mcp/` (the client layer), around the tool functions; `agents/` does not
  import the activity module. Index writes hook the pipeline's existing `on_event`/`on_discovered`
  callbacks from `groundly/cli/subjects.py`.
- Every write is best-effort: exceptions are caught and logged; the work continues.
- Retention: rows older than 7 days are deleted on `session_start` and `index_start`.

**Read model.** `GET /api/activity` (polled by the page every 3 s while visible; gated like every
other read route on the Tailscale socket):

```
{ "hosts":    [{ "client": str|null, "transport": "stdio"|"http",
                 "started_at": str, "last_call_at": str|null,
                 "subject": str|null, "orb": "listening"|"reading"|"mapping"|"writing",
                 "last_done_at": str|null }],
  "indexing": [{ "subject": str, "done": int, "total": int,
                 "current_file": str|null, "started_at": str }],
  "recent":   [{ "at": str, "subject": str|null, "text": str }] }
```

- **A host is live** when it has a `session_start` and no `session_end`, and: stdio, its pid is alive
  on this machine (`os.kill(pid, 0)` on POSIX); http, its last event is within 10 minutes. One entry
  per (pid, session).
  *Builder's call (settled in review):* on Windows `os.kill(pid, 0)` terminates the process, so
  there liveness falls back to the http rule (last event within 10 minutes) for stdio hosts and
  index runs alike. No new dependency.
  `ponytail:` pid reuse can make a dead stdio host look alive until the 7-day prune; add a start-time
  check if it shows up.
- **Orb per host** (*settled in review:* tool calls can run far longer than any fixed window):
  - a `tool_call` with no matching `tool_end` yet → its tool's state: `search`, `get_page`,
    `outline`, `topic`, `progress`, `list_subjects` → `reading`; `submit_tree` → `mapping`;
    `submit_questions`, `remove_questions`, `review_answers` → `writing`;
  - else a `tool_end` within 5 s → that tool's state (so quick consecutive calls don't flicker);
  - else `listening`. No live host → `hosts` is empty and the page shows idle.
  - `done` is not a server state: each host carries `last_done_at` (newest `tree_accepted`,
    `questions_accepted` or `answers_reviewed`), and the page shows `done` for 2 s when it first
    sees a newer value, so a 3 s poll cannot miss it.
- **Indexing** lists runs with `index_start` and no `index_done`/`index_failed` whose pid is alive.
  *Builder's call:* `index_start` is written once discovery has counted the files.
- **Recent** is the newest 20 of `tree_accepted` ("Built topic map"), `questions_accepted` ("Wrote N
  questions · <topic title>", title looked up in the subject's store, omitted if gone),
  `answers_reviewed` ("Graded N answers") and `index_done` ("Indexed N files").
- Page runs (`claude -p` started by the page) connect through MCP too and so appear as hosts; their
  own phase still drives the run strip through the existing stream.

### Docs (with the commit that needs them)

Decision 53 in `docs/groundly-spec.md` §7 (amending 44 and 52): the check route; the subject
summary fields; page-side export, paired phones included; `activity.db` as a third private local
file. Update the storage section of the spec, `docs/architecture/overview.md` and
`data-model.md` (activity.db), `docs/infrastructure/security.md` (export from the page, Tailscale
reach of export and activity, what activity.db holds), and `.claude/rules/` (the privacy boundary
names `activity.db`; the architecture rules list its writers and reader; conventions note that
export is also a page action). The Connections page's connect command is the documented one:
`claude mcp add --scope user groundly -- groundly mcp`.

## Done means

- pytest covers, per commit:
  - check: right, wrong, multiple, an open question refused, a foreign option, a removed question,
    the Origin/JSON dependency, check and submit agreeing on the same selection;
  - subjects: fields on a mapped and tested subject, omitted without a tree, no `progress.db` created
    by listing;
  - export: the zip's contents with and without materials, no `progress.db` inside, the temp file
    gone after the response, a paired Tailscale session allowed and an unpaired one refused;
  - activity: each writer's events, a failing writer not failing the tool call or index run, liveness
    for a dead pid and a stale http session, the orb mapping, retention, the file not created by a
    read, export not touching it.
- `uv run lint-imports`, ruff, pyrefly and the existing suite pass; the eleven-tool surface test is
  unchanged.
- `web/openapi.json` and `schema.d.ts` regenerated; `vp check` passes.
- `spec-guardian` and `security-reviewer` have read the branch diff.

## Left to the builder

- Exact names of the new Pydantic models and the `quiz`/`activity` functions.
- How the MCP layer obtains the client name and HTTP session id from FastMCP's context.
- Whether the activity writer keeps one connection per process or opens one per write.
- The temp-file mechanics of the export response.
