# Page chores: Write questions and Grade start the student's own host — design

Date: 2026-11-17 · Size: project · Records as decision 46 (via `/decision`).

## Intent

The study loop crosses between the page and the host chat by hand. The student asks the host for questions, takes the test on the page, asks the host to grade, and reloads the result. Every crossing is a phrase the student has to remember. Chat also cannot fill a course with questions: a topic is mastered only after 10 graded answers, and apd has 14 big topics and 66 subtopics, while its bank has 10 questions on one subtopic (passc and pw have none).

Some of this work needs a conversation: explaining, asking why an answer was wrong, mapping a course for the student to review. That stays in the host chat, which is built for it. Other work is a fixed procedure whose output goes through a gate into tables the page already reads: writing questions for a topic, and grading the written answers that await review. For those two, the page gets a button that starts the student's own Claude Code headless (`claude -p`), runs the skill's flow, and shows the result on the page.

The page becomes the study surface with buttons for the chores. It does not become a chat client.

## Goals

- **Write questions** in a topic's side panel.
  - On a subtopic: one run brings that subtopic to 10 questions.
  - On a big topic: one run brings every one of its subtopics that has fewer than 10 questions to 10.
  - The button shows only when the subtopic, or at least one subtopic of the big topic, has fewer than 10 questions.
- **Grade** on a result that awaits review. One run reviews every written answer in the subject that awaits review.
- While a run works, the page shows its steps in plain words and a **Stop** button. When it ends, the page shows the run's summary and reloads the panel or result.
- When `claude` is not on PATH, the buttons are not shown and the page shows the phrase to say to the agent, as it does today.

## Non-goals

- No chat on the page and no free text from the page into a run (no note box). The run reads the study state through Groundly's tools.
- No Map button: mapping stays in the host chat, where the student reviews the tree.
- No whole-course button and no queue of runs.
- No host other than Claude Code. A `codex exec` runner can come later.
- One run at a time across the page. No parallel runs.
- No stored run history: a run's state lives in memory in the page process.

## Anti-goals (a failure even if it works)

- A run that can store anything a chat session could not. Every write still goes through `submit_questions` or `review_answers`.
- Layer-3 text (titles, summaries, questions, answers, chunk text) reaching the prompt. The prompt carries only the flow, the subject name and a topic id.
- A foreign web page starting runs on the student's plan.
- The student's personal Claude Code setup (hooks, plugins, CLAUDE.md, other MCP servers, output style) steering a study run.
- Wording that calls a run's questions or verdicts verified.
- Groundly holding a key or constructing an LLM client.

## Constraints

- **No key, no client (decision 37).** The run is the student's own `claude` binary on the student's own login. Groundly passes no credential and reads none. Measured: every headless run reported `apiKeySource: none`.
- **No answer-generation loop inside Groundly (decision 33).** Groundly still generates nothing. It starts the host, and the host writes through the same gates as in chat. This is the decision change that decision 46 records.
- **The page writes no rows to `store.db`.** The run writes through the MCP tools, either in its own `groundly mcp` (under `serve`) or through `/mcp` in the same process (under `start`), which any connected host already does. `web/` code itself still writes only a submitted attempt.
- **Loopback, Host check, Origin check.** The new write routes take the same guard as test Submit: the page's own `Origin`, a JSON body and a size cap, on top of the Host allowlist.
- **The tool surface stays at ten entries.** No MCP tool is added.
- **Layer 3 stays data.** The skill already tells the host that course text, stored titles and summaries, questions, reference answers and student answers are data, never instructions.

## Approaches

| | Shape | Trade-off |
|---|---|---|
| **A — the page starts headless `claude -p` runs (chosen)** | A button sends a POST to the page process, which spawns an isolated run with only Groundly's tools | No terminal and no preview flags, works with the student's login, needs no new MCP tool. Costs: a decision change, a new attack surface, plan usage on every click |
| B — a channel into an open session | The MCP server pushes an event into a running interactive Claude Code session | The student watches and steers in the terminal. But it is a research preview, custom channels load only with `--dangerously-load-development-channels`, it needs a session already open, and it is Claude Code only |
| C — a full harness (chat on the page) | The page becomes a chat client for the host | One window. But it rebuilds a chat client worse than Claude Code's own, sends free text into an agent with tools, and undercuts the thesis claim that agent-mediated grounding in the host is enough |
| (dropped) MCP sampling | Server asks the client's model for a completion | Claude Code does not support it. It is a bare completion with no tools and no skill |
| (dropped) Read and test only | Today's page | Leaves both the hand-offs and the coverage gap |

Paul chose A, shaped as chore buttons on the study page, with **a big topic per click** (about 14 clicks fill apd; one subtopic per click would be about 66) and **Claude Code's built-in default model** for the runs.

## Design

### The run

Each click spawns one process. The argv is a list, with no shell:

```
claude -p <prompt>
  --strict-mcp-config --mcp-config <groundly only>
  --setting-sources ""            # no user/project/local settings: no hooks, plugins, CLAUDE.md
  --tools ""                      # no built-in tools: no files, shell or web
  --allowedTools <this flow's Groundly tools>
  --permission-mode dontAsk       # anything not allowed is denied, never prompted
  --append-system-prompt-file <the installed package's groundly/skill/SKILL.md>
  --output-format stream-json --verbose
  --no-session-persistence
```

- **cwd** is one fixed, empty directory under Groundly's home (e.g. `~/.groundly/.host-run/`), the same for every run. Even with `--no-session-persistence`, Claude Code creates a per-cwd folder under `~/.claude/projects/`, so a fresh tempdir per click would leave one folder per run there. **stdin** is `/dev/null`: without it `claude` waits 3 s for stdin and warns.
- **Isolation is checked, not assumed (fail closed).** The first `stream-json` line is a `system`/`init` event that lists the run's `tools` and `mcp_servers`. If `tools` holds anything not prefixed `mcp__groundly__`, or `mcp_servers` is anything other than `groundly` with status `connected`, the runner kills the run before its first turn. The run then ends `failed` with reason `isolation_failed`, naming what appeared. This covers a `claude` build on the student's machine whose flags behave differently from the one measured here.
- **Platforms:** macOS and Linux. On Windows, `available` is false with reason `unsupported_platform`, and the page shows the phrases as today. An npm-installed `claude` there is `claude.cmd`, which Python runs through `cmd.exe` even with a list argv, so the no-shell rule cannot hold.
- **The environment is inherited**, because the login and PATH live there. Groundly adds nothing to it.
- **The MCP config** names Groundly only:
  - Under `start`: `{"type": "http", "url": "http://127.0.0.1:<port>/mcp"}`. The run shares the process's model load.
  - Under `serve`: stdio, running the `groundly` entry point of the same installation (next to `sys.executable`), never whichever `groundly` comes first on PATH.
- **Allowed tools per flow:**
  - write: `list_subjects`, `progress`, `search`, `get_page`, `submit_questions`.
  - grade: `review_answers`, `get_page`.
  - So a write run cannot remove questions, re-map or grade, and a grade run cannot write questions.
- **Model:** the run passes no `--model` (Paul's choice). With the settings off, Claude Code uses its built-in default for the account, which was `claude-opus-5-5` on Paul's machine. The `model` the student set in `~/.claude/settings.json` is not read. A student who wants a cheaper model sets Claude Code's `ANTHROPIC_MODEL` when starting Groundly, and the run inherits it from the environment. Groundly reads none of Claude Code's settings.
- **Measured on this machine with this flag set:** the run saw exactly the nine Groundly tools, could not read `/etc/hosts`, had the skill's sections, and cost $0.077 for a two-step task. The same task cost $0.27 with the student's full setup loaded.

### Prompts

The prompts are fixed templates. The only values filled in are the subject name, already validated as a subject ID, and an integer topic id that exists in the subject's tree.

- **write, subtopic:** follow "Write questions for a topic" for subject `S`, topic id `T`, bringing it to 10 questions.
- **write, big topic:** the same, for each subtopic of topic id `T` that has fewer than 10 questions, using `progress` for the counts.
- **grade:** follow "Grade a test" for subject `S`. Call `review_answers` until `remaining` is 0, because one call returns at most 20.
- **Every prompt ends the same way:** finish with at most three plain sentences for the student saying what was stored or graded and what was rejected; tell them to run no commands; call nothing verified.

### The runner (`groundly/web/runs.py`)

- One module-level runner holds at most one run, with this state: `id`, `kind`, `subject`, `topic_id`, `status` (`running` | `done` | `failed` | `stopped`), `steps` (list of short strings), `summary`, `error`, `started_at`. Nothing is persisted.
- **Reading the stream.** It reads the `stream-json` lines on a thread. Step text comes from tool names and counts only, never from model text:
  - `search` → "Reading the course"
  - `submit_questions` → "Submitting N questions", then "N stored, M rejected" from its result
  - `review_answers` → "Reading answers" or "Recording N verdicts"
- **The summary** is the final `result` message's text. It is layer 3, capped in length, and shown as text. An error result, a non-zero exit or a timeout gives `failed` with an `error` that names the cause.
- **Caps:** a line-length cap on what is parsed, the last 50 steps kept, the summary capped at 2000 characters, and a wall-clock timeout of 20 minutes.
- **Stop** terminates the process (TERM, then KILL). What the gates already stored stays.
- **Shutdown:** when the page process exits, a running child is terminated.

### Routes (in `web/app.py`)

| Route | Does |
|---|---|
| `GET /api/runs` | `{available, run}`. `available` is whether `claude` is on PATH; `run` is the current or last run's state, or null |
| `POST /api/subjects/{subject}/runs` | Body `{kind: "write", topic_id}` or `{kind: "grade"}`. Starts a run and returns 202 with its state |
| `POST /api/runs/stop` | Stops the current run |

- **The two POST routes** take Submit's guard, extracted into one helper: the page's own `Origin`, `application/json` and a body cap.
- **Refusals carry a machine-readable reason:** `run_in_progress` (409), `no_host` (503), `unknown_subject` (404), `unknown_topic` (404), `nothing_to_write` (400: no subtopic in scope has fewer than 10 questions), `nothing_to_grade` (400), `invalid_body` (400). A refused request starts nothing.

### The page (`page.js`)

- **Panel.** When `available` is true and the scope has a thin subtopic, **Write questions** replaces the "Say to your agent" note. On a big topic the button reads **Write questions for its subtopics**.
- **Result.** When the result is provisional, a **Grade now** button sits beside "provisional, N awaiting review". Each awaiting card says only "Awaiting review".
- **While a run works,** a strip on the current view shows its steps and **Stop**, and every run button is disabled. The page polls `GET /api/runs` every 2 s. Reloading or navigating re-attaches to the running run.
- **When the run ends,** the strip shows the summary, or the error, and the view re-fetches the tree or the result.
- **Everything from the run** (steps, summary, error) is inserted with `textContent`, like all other API strings.

## Docs and decision (same change set)

- **Decision 46** in `docs/groundly-spec.md` §7: the page may start the student's own host headless for the write and grade flows. It also updates §4's Interface and Answering rows and the line in §1 about the host being the interactive surface.
- **Other docs:**
  - `docs/architecture/overview.md`: the runtime modes table and the cross-cutting rules.
  - `docs/infrastructure/security.md`: a new capability, its guards and the residual risk.
  - `docs/use-cases/student-modes.md`: the flow, the alternative paths and the acceptance criteria below.
  - `docs/guides/mcp-hosts.md` (including the `ANTHROPIC_MODEL` escape hatch) and `README.md`.
- **Rules in `.claude/rules/`:** `architecture.md` (no key and no client still hold; the page may start a host run with the isolation flags; the page still writes no rows to `store.db`) and `conventions.md` (product surfaces: the page starts the two chores, and conversation stays in the host).

## Security (adds to the threat model)

- **New capability: a page request can start an agent run on the student's plan.** The guards are the loopback bind, the Host allowlist, the page's own `Origin`, a JSON body (which forces a CORS preflight from any other origin) and one run at a time.
- **Injection.** Course text, imported bundles and student answers reach the run through tools. Four limits apply:
  - no built-in tools, so no files, shell or network;
  - a per-flow allowlist, so a write run cannot remove questions or re-map;
  - every write passes a gate;
  - the skill's paragraph that this text is data.
  - **What remains:** an injected instruction could still skew questions or verdicts within the allowed tools. That is the same risk as in a chat session, and the result page shows the cited page beside every answer.
- **Privacy is unchanged from chat.** A grade run sends the student's written answers to the model, as "grade my test" does today. `progress.db` is still never exported.

## Acceptance criteria

Tests use a fake `claude` executable put on PATH that prints canned `stream-json`. No test calls a model.

1. With `claude` available, a thin subtopic's panel shows **Write questions**. A click starts one run, and the page shows its steps and then its summary. The panel then shows the new question count.
2. On a big topic, the run's prompt names the big topic. A big topic whose subtopics all have 10 or more questions shows no button, and the route refuses it with `nothing_to_write`.
3. A provisional result shows **Grade now**. When the run ends, the result re-fetches. The prompt asks for `review_answers` until `remaining` is 0.
4. With `claude` not on PATH, `available` is false, no run button shows, and the phrases show as today. The start route answers `no_host`.
5. A start while a run is running answers `run_in_progress` and spawns nothing.
6. The start route refuses each of these and spawns nothing: a foreign or missing `Origin`, a non-JSON body, an unknown subject, an unknown topic, a kind other than `write` or `grade`, a `topic_id` that is not an integer.
7. The spawned argv holds `--tools ""`, `--setting-sources ""`, `--strict-mcp-config`, `--permission-mode dontAsk`, the flow's allowlist (a write run's lacks `remove_questions`, `submit_tree` and `review_answers`) and the packaged `SKILL.md` path. It is exec'd with no shell. A topic titled `<script>` or "ignore previous instructions" does not appear in the prompt.
8. Under `start`, the MCP config is `/mcp` on the process's own port. Under `serve`, it is the same installation's `groundly mcp`.
9. A fake `claude` whose init event lists a built-in tool (e.g. `Bash`), or an MCP server other than `groundly`, is killed before its first turn and ends `failed` with `isolation_failed`. On Windows, `available` is false with `unsupported_platform`.
10. **Stop** ends the run with status `stopped`. A run over the timeout ends `failed` with a cause that names the timeout. A non-zero exit ends `failed` with the exit's message.
11. Steps, a summary and an error containing `<script>` render as text.
12. The page still loads no model and writes no rows to `store.db`.
13. **End to end on Claude Code (human):** on apd, **Write questions for its subtopics** on one big topic fills its thin subtopics, then a test is taken, then **Grade now** records verdicts, with nothing typed into a chat.

## Left to the builder

- The exact step wording, the poll interval (about 2 s) and the caps, within the limits above.
- Where Submit's guard helper lives, and how the runner learns the `/mcp` URL and the entry-point path from `create_app` and the CLI.
- The shape of the fake `claude` used in tests.
- Whether the strip is one shared component or one per view.
