# stdio `groundly mcp` forwards model calls to a running `groundly start`

Status: draft for approval (2026-11-27). Becomes decision 54, amending decision 45.

## Intent

A student runs several agent hosts at once: Claude Code sessions, a Codex run whose subagents each spawn
their own MCP server, agy, and the page's own runs. Every host is registered over stdio (`groundly mcp`),
which is the zero-config path the connect hints print, so each one that searches or submits loads
bge-m3 and the reranker itself: about 2 GB per process. A Codex map run alone started three.

Decision 45 made `groundly start` the one process that holds the models, but only hosts registered by
URL reach it, and switching every host to a URL breaks the zero-key, zero-running rule (every tool fails
when `start` is down) and loses the stdio host card's pid liveness.

Measured on an M1 Pro (gate workload, 30 nodes): one, two and three processes give 0.89, 0.97 and 0.92
nodes/s in total, at 2.3, 4.2 and 6.0 GB, with cold loads growing from 3.4 s to 14 s. One GPU is
saturated by one process, so extra model copies buy memory pressure, not speed. Sharing one warm copy is
the whole win.

## Goals

- When `start` runs, a stdio `groundly mcp` sends the three model tools (`search`, `submit_tree`,
  `submit_questions`) to it and loads no model itself.
- When `start` does not run, or is a different Groundly version, the stdio server serves the call
  locally exactly as today.
- No host config changes. The connect hints stay `... -- groundly mcp`.
- The page's host cards stay one card per stdio process, with pid liveness, and a forwarded call is
  never counted twice.

## Non-goals

- Index runs keep their own embedder and extraction worker; nothing exposes embedding over MCP and the
  tool surface stays the pinned eleven entries.
- No pool of model workers in `start`. Revisit only on a machine with several GPUs, measured.
- No cross-session batching of forwards (the only thing that would raise throughput past one process).
- `groundly mcp --http` does not forward.
- `start` is never started automatically.

## Anti-goals

- A forwarded write is never retried locally after it may have reached `start`: that could store a
  tree or questions twice.
- A forward never goes to a port the student's own `start` did not announce.
- No new MCP tool, no change to tool names, arguments, results or descriptions.

## Approaches considered

1. **Per-call forwarding in the stdio server (chosen).** A middleware in `groundly mcp` intercepts the
   three model tools, finds a live `start`, calls the same tool there with a FastMCP client, and returns
   its result; anything else, or no `start`, runs locally. Keeps stdio config, pid-based cards and the
   zero-running fallback; costs a small forwarding module and a runfile.
2. **Whole-surface proxy decided at spawn** (`create_proxy`). Less code, but a session launched before
   `start` keeps its own copy for its life, a session launched after it fails every tool when `start`
   stops, and both servers log the call.
3. **HTTP-only registration.** One model copy, but every tool fails without `start`, each host needs its
   own URL config (agy's support unverified), and cards fall back to recency.
4. **stdio launches `start` itself.** Rejected: a hidden long-lived process that owns a port and outlives
   the session that started it.

## Design

### Discovery: a runfile, not a port probe

`groundly start` writes `start.json` under `groundly_home()` (so `GROUNDLY_HOME` and test homes are
respected; mode `0600`) once its sockets are bound:

```json
{"pid": 4242, "port": 8765, "version": "0.9.0"}
```

It removes the file on exit, only if the file still names its own pid (a second `start` on another port
may have replaced it). A crash (an OOM kill of a 2 GB process is plausible) leaves a stale file; readers
ignore one whose pid is not alive.

Liveness here is stricter than the activity log's: `os.kill(pid, 0)` must succeed. `PermissionError`
counts as **not alive** (the activity log counts it as alive), because `start` always runs as the
student, so a pid owned by another account is a reused pid, never their `start`.

Why a runfile: it covers `--port N`, and only the student's own account can write their home, so another
local user's process listening on 8765 is never mistaken for Groundly. `start.json` holds no course text,
lives outside `subjects/`, and is never exported.

No per-run secret (decided by the designer, open to change): a forward can still reach a foreign
process only if `start` died without removing the file, the student's own account then reused its pid,
and another account bound the port in between, and it then also has to claim to be `groundly` at the
same version. A secret in the runfile would not close that gap anyway: the client would hand it to
whoever answers. Proving the server knows the secret needs a challenge route on `start`. That's the
upgrade path if a multi-user machine ever matters; the threat model already accepts unauthenticated MCP
on loopback (security.md).

**Windows never forwards** (decided by the designer): pid liveness can't be checked there (`os.kill` terminates
the process) and `0600` means little, so the runfile can't be trusted. `start` does not write it on
Windows, and the stdio server always serves locally, as today. Page runs already refuse Windows.

### The forward

`groundly/mcp/forward.py` holds `ForwardMiddleware`, registered by `groundly mcp` for stdio only, inside
`ActivityMiddleware` (so the stdio server logs the call once, as today). For `search`, `submit_tree` and
`submit_questions`:

1. Read `start.json`. Missing, unreadable, pid not alive, or `version` not equal to this Groundly's
   version → call the local tool.
2. Open a FastMCP client to `http://127.0.0.1:<port>/mcp` with the header `X-Groundly-Forwarded: 1`.
   Connection refused or the server's `serverInfo` not `groundly` at the same version → local.
3. Call the same tool with the same arguments, with no client-side read timeout (a large `submit_tree`
   can run for minutes; the host's own tool timeout still applies).
4. A result, including a tool error, goes back to the host as is: content, structured content and the
   error flag. A tool error keeps its message.
5. Any failure after the call was sent (connection dropped, `start` shut down mid-call) is a tool error,
   `"groundly start stopped during the call — retry it"`, never a local retry. The host's retry finds
   no runfile and runs locally.

One client per forwarded call. The connect and discover cost a few milliseconds against a call that
takes a second or more.

Every other tool and resource stays local: they read SQLite and load no model.

### Activity

- The stdio server's `ActivityMiddleware` logs the forwarded call under its own pid, so the card keeps
  pid liveness and shows the host's own client name. Acceptance events (`tree_accepted`,
  `questions_accepted`) come from the forwarded result's structured content, as today.
- `start`'s `ActivityMiddleware` logs nothing for a request carrying `X-Groundly-Forwarded`. Display
  only, like `X-Groundly-Page-Run`: any local client could send it to hide its calls from the card.

### Concurrency

Unchanged rules carry the load: `start` serializes model forwards per process (MLX forward lock,
reranker scoring lock), so a host's interactive `search` waits at most one forward behind a page run's
or another host's `submit_tree`, not the whole tree. The gates' write-time rechecks (topic tree's
`StaleTopicError`, question bank's `QuestionConflictError`) already hold when calls from several hosts
arrive at `start`.

## Base

Builds on the work on `fix/activity-host-identity`, which is not yet on `main`: the server's own
`serverInfo` version (step 2 compares it), the per-request header reading in `ActivityMiddleware`
(`X-Groundly-Page-Run`, which `X-Groundly-Forwarded` mirrors), and the gates' write-time rechecks
(`QuestionConflictError`, which the Concurrency section relies on). That work is committed first, and
this feature goes on a new branch cut from it. Decision 54 is unused on both branches.

## Docs to change (with `/decision`, decision 54)

- `docs/groundly-spec.md`: decision 54; amend decision 45's "only hosts registered by URL" reading;
  interface row.
- `docs/architecture/overview.md`: stdio forwards model tools to `start` when it runs.
- `docs/architecture/data-model.md`: `start.json` in the layout, never exported.
- `docs/infrastructure/security.md`: "stdio MCP has no network surface" becomes "stdio MCP opens one
  outbound loopback connection, to the port the student's own `start` announced in a `0600` runfile";
  `X-Groundly-Forwarded` is display only; the residual stale-runfile case and "Windows never forwards"
  are stated.
- `.claude/rules/architecture.md`: the storage and concurrency section names the forward.

## Left to the builder

- Module layout inside `groundly/mcp/` and how `start` writes and removes the runfile (`atexit` plus the
  `_run` `finally`).
- Test harness: an in-process `start` app on an ephemeral port, or an injected transport.
- Exact wording of the stdio server's log line when it falls back.
