# Groundly: use cases and interfaces

**Status:** proposed design, 2026-11-09. Not implemented. Adopting it changes decisions 40 and 41 (indexing becomes an MCP operation) and is recorded through `/decision` before any code changes.

**Purpose:** the complete use-case catalogue for the thesis, and the CLI verbs and MCP tools derived from it. Sections 1–4 are written to go into the thesis document; section 5 lists the design calls made while writing it, for review.

---

## 1. Scope and actors

### 1.1 System boundary

Groundly is one Python package installed on the student's machine. Inside the boundary: the MCP server (`groundly mcp`), the command-line interface (`groundly …`), the read-only topic-tree page (`groundly serve`), the local embedding and reranking models, and the per-subject stores under `~/.groundly/<subject>/` (`store.db`, `materials/`, `manifest.json`, `progress.db`).

Outside the boundary: the host agent and its language model, the host's own configuration and approval prompts, the student's files before they are indexed, the model download hubs, and other Groundly users' machines. Groundly runs no language model and holds no API key; every step that needs reasoning or generated text happens in the host.

### 1.2 Actors

| Actor | Kind | Role |
|---|---|---|
| **Student** | primary, human | Owns the machine and the data. Talks to the host agent, runs CLI verbs, reads the topic-tree page. |
| **Host agent** | primary, system | An MCP client (Claude Code, Claude Desktop, Codex) acting for the student. Calls Groundly's tools, composes answers, writes summaries and questions. Untrusted with respect to what it submits: everything it generates passes a gate before it is stored. |
| **Bundle author** | secondary, human | Another Groundly user whose exported knowledge base the student imports. Their content is untrusted data. |
| **Model hub** | secondary, system | Hugging Face (bge-m3 and the reranker) and modelscope.cn (sha256-pinned OCR models). The only network destinations Groundly contacts. |

### 1.3 Status of each use case

- **Built** — implemented, with acceptance criteria passing.
- **Changed** — built, and this design changes it (indexing and lifecycle operations become available to the host agent).
- **Planned** — has its own specification before implementation (sub-project 3).
- **Idea** — future work, not scheduled.

---

## 2. Use-case catalogue

### 2.1 Overview

| ID | Use case | Primary actor | Surface | Status |
|---|---|---|---|---|
| **Setup** |||||
| UC-01 | Connect a host agent | Student | CLI | Built |
| UC-02 | Manage local models and configuration | Student | CLI | Built |
| **Knowledge base** |||||
| UC-03 | Index materials | Host agent, Student | MCP, CLI | Changed |
| UC-04 | Manage materials | Host agent, Student | MCP, CLI | Changed |
| UC-05 | Manage subjects | Host agent, Student | MCP, CLI | Changed |
| UC-06 | Grounded search | Host agent, Student | MCP, CLI | Built |
| **Sharing** |||||
| UC-07 | Export a knowledge base | Host agent, Student | MCP, CLI | Changed |
| UC-08 | Import a knowledge base | Host agent, Student | MCP, CLI | Changed |
| **Study** |||||
| UC-09 | Build the topic tree | Host agent | MCP | Built |
| UC-10 | View the topic tree | Student | Page | Built |
| UC-11 | Build a verified question bank | Host agent | MCP | Planned |
| UC-12 | Take a quiz | Student, Host agent | MCP | Planned |
| UC-13 | Track mastery | Student, Host agent | MCP, Page | Planned |
| UC-14 | Solve a coding challenge | Student, Host agent | MCP | Idea |

In every host-agent use case the student starts the interaction by asking the host in conversation; the host then calls Groundly. The student never needs to name a tool.

### 2.2 Setup

#### UC-01 — Connect a host agent

**Actor:** student. **Surface:** CLI. **Status:** built.
**Goal:** the host agent can call Groundly's tools and knows how to map a course.

1. The student installs the package (`uv tool install groundly`).
2. The student registers the server with the host, using the host's own command (for Claude Code: `claude mcp add groundly -- groundly mcp`). Groundly never edits another tool's configuration.
3. The student runs `groundly skill install`, which copies the packaged topic-tree skill into the host's skills folder; bare `groundly skill` reports not installed / installed / out of date.

**Alternative:** a host that speaks only HTTP connects to `groundly mcp --http`, which binds 127.0.0.1 with DNS-rebinding protection.
**Guarantee:** the server starts without loading any model; models load on first use.

#### UC-02 — Manage local models and configuration

**Actor:** student; secondary: model hub. **Surface:** CLI. **Status:** built.
**Goal:** inspect and control the local models and the few settings Groundly has.

1. `groundly models` shows whether bge-m3 and the reranker are cached; `models install` downloads and verifies them ahead of time, and `models uninstall` removes both.
2. `groundly config` shows the settings; `config set <key> <value>` changes one (for example `ingestion.timeout_seconds`, `retrieval.top_k`), with a plain-language error for an unknown key or a bad value.

**Guarantee:** every other use case works with no configuration file at all, and none asks for a key. Without step 1, models download on first use.

### 2.3 Knowledge base

#### UC-03 — Index materials

**Actor:** host agent (main path) or student. **Surface:** MCP `index`, CLI `groundly index`. **Status:** changed.
**Goal:** turn course files into cited, searchable chunks of a subject.
**Preconditions:** the paths exist on the student's machine. The subject is created by its first indexing.

**Main flow (host agent)**

1. The student asks the host to add materials, for example "add this week's lecture, `~/Downloads/curs7.pdf`, to apd".
2. The host calls `index(subject, paths)`. The host's approval prompt shows the student the subject and the paths before the call runs.
3. Groundly checks every path before writing anything: each must be absolute and exist. It expands directories, hashes every supported file, skips hashes already indexed, and queues the rest.
4. The call returns at once with each file's initial state (`queued`, `duplicate`, `unsupported`).
5. In the background, each queued file goes through its own transaction: extraction in a subprocess (Docling, OCR for scanned regions), section-aligned chunking with the heading path prepended, dense and sparse embedding, storage. Its state moves `queued → extracting → embedding → indexed`.
6. The host calls `list_materials(subject)` to follow progress, and reports to the student when no file is still in progress.

**Main flow (student, CLI):** `groundly index <subject> <paths…>` runs the same pipeline in the foreground with per-file progress.

**Alternative and error flows**

- **A1 — Scanned PDF or image:** indexed through OCR with page attribution; a file with no readable text even after OCR ends as `extraction_failed` with "no readable text — OCR found nothing to extract".
- **A2 — Parser crash on a broken or hostile file:** the extraction subprocess dies, that file is `extraction_failed`, the run continues.
- **A3 — A path is relative or missing:** nothing is queued; the reply names every bad path (`path_not_absolute`, `path_not_found`).
- **A4 — The subject is already being indexed** by another process: refused with `index_in_progress`; searching keeps working.
- **A5 — Interrupted run** (Ctrl-C, or the host closes and takes the MCP server with it): at most the in-flight file is lost; calling `index` again with the same paths resumes.
- **A6 — Retrying failures:** later runs skip `extraction_failed` files and say so; `retry_failed` re-extracts them.
- **A7 — OCR language:** set once per subject on first indexing (`ocr_lang`); changing it later is refused with a message that it needs a re-index.

**Guarantees:** indexing is idempotent (an unchanged folder embeds nothing; one new file embeds one file). Both surfaces produce identical stores. Every stored chunk carries its document, page and heading path.

#### UC-04 — Manage materials

**Actor:** host agent or student. **Surface:** MCP `list_materials`, `remove_materials`; CLI `groundly list <subject>`, `groundly remove`. **Status:** changed.
**Goal:** see what a subject contains and take materials out of it.

1. The actor lists the subject's materials: filename, state, page count, chunk count, and the failure reason for a failed file.
2. To remove, the actor names one or more materials. Groundly resolves all of them before deleting any; one unknown name removes nothing.
3. Removing a material deletes its chunks from every retrieval channel and its stored copy, and updates the manifest counts.

**Alternative:** a topic-tree node whose sources were all removed stays, flagged "sources removed" (see UC-09).
**Guarantee:** a removed material leaves no retrievable chunk in any channel.

#### UC-05 — Manage subjects

**Actor:** host agent or student. **Surface:** MCP `list_subjects`, `set_title`, `drop_subject`; CLI `groundly list`, `groundly title`, `groundly drop`. **Status:** changed.
**Goal:** see the subjects on this machine, label them, and delete one.

1. Listing shows every subject with its title, material and chunk counts, and whether it has a topic tree. It works from any working directory.
2. Setting a title gives the subject a label for people and models. The subject's name stays its identifier in commands, paths and citations; an empty title clears it.
3. Dropping a subject deletes its whole directory, including `progress.db`. It cannot be undone.

**Alternatives:** a subject with a corrupt manifest is listed without a title; a subject with a damaged store is listed with zero counts and an error, and the others are still listed.

#### UC-06 — Grounded search

**Actor:** host agent or student. **Surface:** MCP `search`, `get_page`, resource `groundly://<subject>/<file>`; CLI `groundly search`. **Status:** built.
**Goal:** answer from the course's own material, with citations the student can open.
**Precondition:** the subject has at least one indexed material.

1. The student asks the host a question about a subject.
2. The host calls `search(subject, query)`. Groundly runs dense, sparse and BM25 retrieval, fuses the rankings (RRF), reranks with a cross-encoder, and returns the top chunks verbatim, each with its filename, page, heading path and a `groundly://` URI.
3. The host composes the answer from those chunks and cites them.
4. To show more context, the host passes a cited URI to `get_page`, which returns exactly that page; read as a resource, the URI returns the whole document.

**Guarantees:** every hit resolves to the correct page of the correct material; CLI and MCP return the same hits for the same query. Grounding of the composed answer is best-effort: the host composes, and Groundly does not claim enforcement over text it did not produce.

### 2.4 Sharing

#### UC-07 — Export a knowledge base

**Actor:** host agent or student. **Surface:** MCP `export_subject`, CLI `groundly export`. **Status:** changed.
**Goal:** turn a subject into one file that another student can import.

1. The actor names the subject and an output path (absolute on MCP; `./<subject>.groundly` by default on the CLI).
2. Groundly writes a `.groundly` bundle from a fixed list: the manifest, `store.db` (chunks, vectors, sparse and text indexes, the topic tree) and the indexed original files. With `no materials`, originals are left out and citations show chunk text instead of opening the PDF.
3. The result states plainly that the bundle contains everything indexed in the subject, including its topic map. The host relays this to the student.

**Guarantee:** `progress.db` is never exported and never read by export code.

#### UC-08 — Import a knowledge base

**Actor:** host agent or student; secondary: bundle author. **Surface:** MCP `import_bundle`, CLI `groundly import`. **Status:** changed.
**Goal:** use a knowledge base someone else built, without re-indexing it.

1. The actor names a bundle file.
2. Groundly validates the manifest before extracting anything (format, schema version, counts), extracts safely (no entry may escape the target directory), and checks the imported store: expected tables only, counts match, the topic tree is two levels with no dangling references.
3. If the embedding pin matches, the vectors are used as they are; search works at once. A fresh, empty `progress.db` is created.

**Alternatives**

- **A1 — Name collision:** refused unless the actor imports under another name or explicitly asks to replace the existing subject.
- **A2 — Embedding pin mismatch:** refused unless the actor asks to re-embed from the chunk text (local, minutes); re-embedding runs like indexing (UC-03).
- **A3 — Bundle from an incompatible version, or with a malformed tree:** refused with a message naming the cause.

**Guarantees:** imported content (chunk text, titles, summaries) is data, never instructions. Importing never touches the importer's existing `progress.db` and never overwrites a subject without an explicit request.

### 2.5 Study

#### UC-09 — Build the topic tree

**Actor:** host agent, at the student's request. **Surface:** MCP `outline`, `submit_tree`. **Status:** built.
**Goal:** a two-level map of the subject, big topics in course order with subtopics, each node with a short summary and the course passages behind it.
**Precondition:** UC-01 done (server connected, skill installed).

1. The student asks the host to map the course.
2. The host calls `list_subjects`. If the subject is missing or has new files, it asks the student for the folder and indexes it (UC-03), waiting until indexing finishes.
3. The host calls `outline(subject)`: every indexed material with its page count and headings, and the current tree with topic ids. No chunk text.
4. The host merges overlapping lectures, orders big topics by course order and writes a summary of at most 500 characters per node.
5. The host calls `submit_tree(subject, tree)`. It cites nothing: Groundly retrieves on each node's title and summary with the reranker on, and keeps up to five passages above the relevance cutoff as that node's sources.
6. The whole tree is stored, or the whole tree is rejected with every failing node and a machine-readable reason (for an off-topic node: its best score and the nearest heading, so the host can reword it in the course's terms).

**Alternatives:** re-running keeps the ids the host sends back; a new node gets a new id, and a deleted id is never reused. A node whose sources were removed is flagged until the next accepted submission.
**Guarantee:** the gate shows that each topic is in the course. It does not show that a summary is true, and no surface calls a summary verified.

#### UC-10 — View the topic tree

**Actor:** student. **Surface:** CLI `groundly serve`, then the browser page. **Status:** built.
**Goal:** see the course map and open the passages behind each topic.

1. `groundly serve` prints the URL, opens the browser and serves every subject on 127.0.0.1.
2. The student picks a subject and opens a node: its title, summary, progress and sources (`file.pdf · p.17`).
3. A stored PDF source opens at that page; any other source shows the cited chunk text.

**Alternatives:** with no tree yet, the page shows the sentence to say to the host. A foreign `Host` header is refused.
**Guarantees:** the page loads no model and writes nothing to `store.db`; titles, summaries and chunk text are inserted as text, never as HTML.

#### UC-11 — Build a verified question bank

**Actor:** host agent. **Surface:** MCP `submit_questions`. **Status:** planned (sub-project 3).
**Goal:** store questions for a topic, each one checked against the course.
**Precondition:** the subject has a topic tree.

1. The host writes multiple-choice or open questions for one topic, each citing the chunks it is based on.
2. The host calls `submit_questions(subject, topic_id, questions)`.
3. The verifier checks each question: its cited chunk ids resolve; re-retrieving the question's own text surfaces at least one of them; a multiple-choice item has exactly one key and distinct distractors.
4. Passing questions are stored in the question bank; failing ones are returned with machine-readable reasons so the host can rewrite them.

**Alternative:** a code question is refused at the door (`code_questions_unsupported`) until UC-14's runner exists.
**Guarantee:** nothing unverified enters the question bank.

#### UC-12 — Take a quiz

**Actor:** student, through the host agent. **Surface:** MCP `quiz`, `record_answer`. **Status:** planned (sub-project 3).
**Goal:** practise a topic and have the result count towards mastery.

1. The student asks the host to quiz them on a topic.
2. The host calls `quiz(subject, topic_id)` and receives questions from the bank (if there are too few, it first adds some through UC-11).
3. The host asks each question in the conversation.
4. For a multiple-choice question, the host passes the student's choice to `record_answer`; Groundly grades it and returns the result with the cited source.
5. For an open question, the host judges the student's answer against the reference answer and the cited chunks, then records its verdict through `record_answer`.

**Guarantee:** answers are written only to `progress.db`, which is never exported.

#### UC-13 — Track mastery

**Actor:** student (page) or host agent. **Surface:** MCP `progress`, topic-tree page. **Status:** planned (sub-project 3).
**Goal:** know which topics are mastered and what to study next.

1. A topic is mastered when at least ten answers are recorded and at least 90% of the last ten are correct; a big topic is mastered when all its subtopics are.
2. The page shows each node's state in place of "Not started".
3. The host calls `progress(subject)` to suggest what to study next.

**Guarantee:** mastery is computed from `progress.db` alone and leaves the machine only if the student copies it.

#### UC-14 — Solve a coding challenge

**Actor:** student, through the host agent. **Surface:** MCP (extends `submit_questions` and `record_answer`). **Status:** idea.
**Goal:** practise programming topics with answers checked by running them.

1. The host submits a code question with a reference solution and tests.
2. The verifier runs the reference solution in a subprocess (temporary directory, time limit, argument-vector execution, output cap) and stores the question only if it passes its own tests.
3. The student's solution is run the same way, and the result is recorded like any other answer.

**Guarantee:** until the runner exists, no code question enters the bank. The guarantee is unbuilt, never weakened.

---

## 3. Interfaces

### 3.1 How the work is split between the CLI and MCP

Five rules follow from the use cases:

1. **Data lifecycle is available on both surfaces.** Indexing, listing, titling, removing, dropping, searching, exporting and importing (UC-03 to UC-08) each have a CLI verb and an MCP tool. Both call the same service function, so the host agent and the student produce identical results.
2. **Study operations are available only over MCP.** Building the tree, submitting questions and recording answers (UC-09, UC-11 to UC-14) store or consume content a language model produces, and anything conversational belongs to the host. So do the reads written for a model: `outline`, `get_page`, `quiz`, `progress`.
3. **Machine setup is available only on the CLI.** Models, configuration, the skill, starting the MCP server and starting the page (UC-01, UC-02, UC-10) configure the machine Groundly runs on or start Groundly's own processes; a tool call cannot start the server it runs in, and Groundly does not edit the host's configuration.
4. **Confirmation stays with each surface.** The CLI prompts before an irreversible step, and `-y/--yes` is the one way to skip it. MCP has no prompt of its own: destructive tools are annotated so the host asks the student, and an irreversible effect beyond the call's plain meaning (replacing an existing subject, re-embedding) needs an explicit argument.
5. **MCP paths are absolute.** The host starts the server in an arbitrary working directory, so a relative path would mean nothing predictable.

### 3.2 MCP tools

Annotations are MCP tool hints: **R** read-only, **D** destructive, **I** idempotent.

| Tool | Use case | Arguments | Returns | Hint | Status |
|---|---|---|---|---|---|
| `list_subjects` | UC-05, UC-09 | — | subjects with title, counts, `has_tree`, error | R | built |
| `search` | UC-06 | `subject`, `query`, `k?`, `material?` | ranked chunks: text, filename, page, heading path, URI | R | built; `material` new |
| `get_page` | UC-06 | `uri` | that page's chunks, or the whole material | R | built |
| resource `groundly://{subject}/{filename}` | UC-06 | — | the whole document, grouped by page | R | built |
| `outline` | UC-09 | `subject` | materials, pages, headings, current tree with ids | R | built |
| `submit_tree` | UC-09 | `subject`, `tree` | accepted tree with sources, or every failing node with a reason | — | built |
| `index` | UC-03 | `subject`, `paths`, `ocr_lang?`, `retry_failed?` | each file's initial state; the run continues in the background | I | new |
| `list_materials` | UC-03, UC-04 | `subject` | each material: state, pages, chunks, failure reason | R | new |
| `remove_materials` | UC-04 | `subject`, `filenames` | removed materials; nothing removed if any name is unknown | D | new |
| `set_title` | UC-05 | `subject`, `title` | the subject with its new title | I | new |
| `drop_subject` | UC-05 | `subject` | confirmation that the subject is gone | D | new |
| `export_subject` | UC-07 | `subject`, `out`, `include_materials?` | bundle path, size, and the statement of what it contains | — | new |
| `import_bundle` | UC-08 | `path`, `as_name?`, `replace?`, `reembed?` | the imported subject, or the reason it was refused | D | new |
| `submit_questions` | UC-11, UC-14 | `subject`, `topic_id`, `questions` | accepted ids; rejected items with reasons | — | planned |
| `quiz` | UC-12 | `subject`, `topic_id`, `n?` | questions; reference answer and sources for open ones | R | planned |
| `record_answer` | UC-12, UC-14 | `question_id`, `choice` or `verdict` | correct or not, with the cited source | — | planned |
| `progress` | UC-13 | `subject` | each topic's answered count, recent accuracy, mastered | R | planned |

The tool surface grows from six entries to thirteen, and to seventeen with sub-project 3. Each description leads with when to call the tool (for `index`: "when the student gives you course files to add to a subject"). The server's instructions keep stating only the norm, retrieve before answering, and rank no tool above another.

### 3.3 CLI verbs

| Verb | Use case | Form |
|---|---|---|
| `index` | UC-03 | `groundly index <subject> <paths…> [--ocr-lang L] [--retry-failed]` |
| `list` | UC-04, UC-05 | `groundly list [<subject>]` |
| `remove` | UC-04 | `groundly remove <subject> <materials…> [-y]` |
| `title` | UC-05 | `groundly title <subject> <text>` (`""` clears) |
| `drop` | UC-05 | `groundly drop <subject> [-y]` |
| `search` | UC-06 | `groundly search <subject> <query> [-k N] [-m material] [--rerank/--no-rerank]` |
| `export` | UC-07 | `groundly export <subject> [-o path] [--no-materials]` |
| `import` | UC-08 | `groundly import <bundle> [--as name] [-y]` |
| `models` | UC-02 | `groundly models`, `models install [--force]`, `models uninstall [-y]` |
| `config` | UC-02 | `groundly config`, `config set <key> <value>` |
| `skill` | UC-01 | `groundly skill`, `skill install [-y]`, `skill uninstall [-y]` |
| `mcp` | UC-01 | `groundly mcp [--http [--port N]]` |
| `serve` | UC-10 | `groundly serve [--port N]` |

The CLI keeps its thirteen verbs. No study verb is added: quizzes and mastery belong to the host conversation and the page.

### 3.4 Traceability

| Use case | CLI | MCP |
|---|---|---|
| UC-01 Connect a host agent | `skill`, `mcp` | — |
| UC-02 Models and configuration | `models`, `config` | — |
| UC-03 Index materials | `index` | `index`, `list_materials` |
| UC-04 Manage materials | `list <subject>`, `remove` | `list_materials`, `remove_materials` |
| UC-05 Manage subjects | `list`, `title`, `drop` | `list_subjects`, `set_title`, `drop_subject` |
| UC-06 Grounded search | `search` | `search`, `get_page`, resource |
| UC-07 Export | `export` | `export_subject` |
| UC-08 Import | `import` | `import_bundle` |
| UC-09 Build the topic tree | — | `list_subjects`, `index`, `outline`, `submit_tree` |
| UC-10 View the topic tree | `serve` | — |
| UC-11 Question bank | — | `submit_questions` |
| UC-12 Take a quiz | — | `quiz`, `record_answer` |
| UC-13 Track mastery | — (page) | `progress` |
| UC-14 Coding challenge | — | `submit_questions`, `record_answer` |

### 3.5 Indexing through MCP

Indexing is the one operation that does not fit a single tool call. OCR on a scanned lecture takes minutes per file, and a host's tool-call timeout must not decide whether a file gets indexed. So `index` validates and queues, then returns; the work continues in the server.

- **Where it runs.** A background worker in the MCP server process runs the same ingestion pipeline as the CLI, one file per transaction. Extraction stays in its own subprocess, as on the CLI.
- **How the host follows it.** `list_materials` reports each file's state. The host polls it; there is no separate status tool.
- **Lifetime.** The run lives as long as the server process. If the host closes, at most the in-flight file is lost, and the next `index` call with the same paths picks up where it stopped, because already-indexed hashes are skipped.
- **One indexer per subject.** A subject is indexed by at most one process at a time, across the CLI and every MCP server; a second request is refused with `index_in_progress`. Searches keep working during a run (SQLite WAL).
- **Models.** bge-m3 loads on the first `index` or `search` call in that process, never when the server starts.
- **Paths.** Any absolute path on the machine may be indexed. The guard is the host's approval prompt, which shows the paths to the student before the call runs. Groundly copies indexed files into `materials/`, and exports include them, so this prompt is also the point where a private file could enter a bundle; the export result's plain statement of contents is the second check.

### 3.6 Security consequences

- Write and destructive operations become reachable by the host. A prompt-injected host could ask to index a private file, export a subject, or drop one. The guards are the host's approval prompt (shown for every destructive tool, by annotation), explicit arguments for replacement and re-embedding, and the export statement. This is accepted as the cost of host-driven indexing and recorded in the threat model.
- `groundly mcp --http` exposes the same thirteen tools, still bound to 127.0.0.1 with DNS-rebinding protection.
- Import over MCP crosses the same trust boundary as on the CLI: manifest first, safe extraction, store and tree checks, imported content as data.

---

## 4. Changes to existing decisions and documents

| Where | Change |
|---|---|
| Decision 40 | The MCP surface was left unchanged and indexing was a CLI verb; now lifecycle operations are on both surfaces, and the MCP `material` filter that was waiting for a material listing arrives with `list_materials`. |
| Decision 41 | The skill indexed by running `groundly index` in the host's shell; it now calls the `index` tool and waits on `list_materials`. |
| `docs/groundly-spec.md` §1, §3, §4 | Vision ("a student runs `groundly index` once") gains the host path; §3 adopts the UC-01…UC-14 numbering; the architecture sketch lists the new tools. |
| `docs/use-cases/` | Renumbered to this catalogue, with the old IDs kept in a mapping (below). |
| `.claude/rules/conventions.md` | The pinned surface ("exactly these six entries") becomes thirteen; the test pins the new list. |
| `docs/infrastructure/security.md` | Host-reachable write and destructive tools added to the threat model (3.6). |
| `groundly/skill/SKILL.md` | Step 2 calls `index` instead of the shell. |

**Old to new IDs:** UC-01 → UC-03 · UC-02 → UC-06 · UC-03 → UC-04 and UC-05 · UC-30 → UC-07 and UC-08 · topic tree → UC-09 and UC-10 · verified quizzes and mastery → UC-11 to UC-13 · coding challenges → UC-14.

---

## 5. Design calls to review (not for the thesis)

Paul decided: indexing on both surfaces with MCP as the main path; scope includes planned use cases and ideas; the host may do every lifecycle operation, export, import and drop included; any absolute path, guarded by the host's approval prompt.

Calls made while writing, each changeable:

1. **Renumbered all use cases** UC-01…UC-14 in reading order instead of keeping UC-01–03 and UC-30. Old IDs were sparse after the dropped professor, photo and flashcard use cases (UC-10/11, UC-15, UC-20–24), and reusing any of them would be ambiguous in the git history; a fresh sequence reads better in the thesis, with the mapping in section 4.
2. **Split** source management into materials (UC-04) and subjects (UC-05), and sharing into export (UC-07) and import (UC-08): different actors' concerns, and import is the trust boundary.
3. **Background indexing with polling** through `list_materials` instead of a long blocking call or a separate status tool.
4. **`list_materials` as a new tool** rather than extending `outline`, which is large (about 66k characters on apd) and meant for mapping, not progress.
5. **MCP `search` gains `material?`**, the filter decision 40 deferred until hosts could list materials.
6. **One indexer per subject** across processes, refused with `index_in_progress`; the locking mechanism is left to the builder.
7. **Questions are accepted or rejected one by one** in `submit_questions`, unlike the all-or-nothing tree, because questions do not depend on each other.
8. **Multiple-choice answers are graded by Groundly; open answers are judged by the host** and recorded as a verdict. This comes from the earlier quiz planning and is not yet in the docs.
9. **The question bank lives in `store.db`** and travels with bundles (verified, course-level content); answers and mastery stay in `progress.db`.
10. **No new CLI verbs.** Quizzes and progress have no CLI form, following "anything conversational belongs to the host".
11. **HTTP mode exposes the same tools**, destructive ones included, rather than a read-only subset; loopback binding and DNS-rebinding protection are unchanged.
