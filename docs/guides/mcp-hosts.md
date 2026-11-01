# Connecting MCP hosts (Claude Code, Codex) to Groundly

Groundly is an MCP server: `groundly mcp` speaks stdio and is spawned (and
killed) by the host — no daemon to manage. It works from any working
directory (subjects live under `~/.groundly/`).

**Prerequisites:** Groundly installed so `groundly` is on your `PATH`, and at
least one indexed subject (`groundly index <SUBJECT> <files...>` creates and
indexes it). No API key is needed for anything the host can call.

## Claude Code

```sh
claude mcp add groundly -- groundly mcp
```

Add `--scope user` to make it available in every project rather than just the
current one. Alternatively, check a `.mcp.json` into a project to share the
setup:

```json
{
  "mcpServers": {
    "groundly": {
      "command": "groundly",
      "args": ["mcp"]
    }
  }
}
```

Verify with `/mcp` inside Claude Code — `groundly` should list five tools (`list_subjects`, `search`, `get_page`, `outline`, `submit_tree`).

### Map a course (topic tree)

Order matters: **connect first, then install the skill.**

1. Connect the server (above) and restart Claude Code if it was open.
2. Install the skill that tells Claude Code how to build the map:

   ```sh
   groundly skill install
   ```

   It copies one file to `~/.claude/skills/groundly/` and never edits Claude Code's config, which is why connecting stays a separate step. Re-running it is the upgrade path; it asks before replacing a different copy (`-y` skips the question). `groundly skill` shows whether the skill is installed or out of date, and `groundly skill uninstall` removes it.
3. In Claude Code, say *"map my course"* (naming the subject). The skill reads `outline`, drafts a two-level tree and calls `submit_tree`. If a node is rejected as not matching the course, Claude rewords it in the course's own terms and submits again.
4. Run `groundly serve` and open the printed URL (default `http://127.0.0.1:8765/`; `--port N` to change it) to see the map. The page is read-only; every node reads "Not started" until quizzes arrive.

If the skill is installed but the server is not connected, it prints the connect command instead of failing. A subject indexed before the topic tree existed answers searches but fails tree calls with a "no such table" error: `groundly drop <SUBJECT>` and index it again once.

The check on each node shows that its topic appears in your course materials; it does not show that Claude's summary is correct.

## Codex

The five tools work here, but there is no skill setup for Codex yet, so building a topic tree is Claude Code only for now.

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.groundly]
command = "groundly"
args = ["mcp"]
```

(Newer Codex CLI versions also accept `codex mcp add groundly -- groundly
mcp`.)

## HTTP transport (`groundly mcp --http`)

For hosts that connect to a URL instead of spawning a stdio subprocess, run
the same server over Streamable HTTP:

```sh
groundly mcp --http              # binds http://127.0.0.1:8000/mcp
groundly mcp --http --port 9000
```

It binds 127.0.0.1 only. Point the host at the URL:

```sh
claude mcp add --transport http groundly http://127.0.0.1:8000/mcp
```

or in `.mcp.json`:

```json
{
  "mcpServers": {
    "groundly": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

Unlike stdio, you manage the process yourself — the host does not start or
stop it.

## What the host gets

| Tool | What it does |
|---|---|
| `list_subjects` | subjects with material/page/chunk counts |
| `search` | top-k ranked chunks with citations; the host composes the answer |
| `get_page` | takes a citation `uri` from `search` and returns the verbatim chunks on that page |
| `outline` | a subject's materials with their page headings, and its current topic tree with ids |
| `submit_tree` | stores a topic tree the host wrote; Groundly picks each node's sources and rejects the whole tree if any node is not in the course |

Citations carry URIs like `groundly://<subject>/<file>#page=N`; the same
document is readable as an MCP resource (`groundly://<subject>/<file>`,
chunks grouped by page). Everything stays local: the only network traffic is
model downloads (Hugging Face, and RapidOCR models for a non-default `--ocr-lang`).

Try it:

> Using groundly, what subjects do I have indexed?
>
> Using groundly, search my OS subject for what causes a deadlock, then open the page it cites.

If the host reports the server as failed to start, run `groundly mcp` in a
terminal yourself — import/config errors print there. (It waiting silently on
stdin means it's healthy; Ctrl-C out.)
