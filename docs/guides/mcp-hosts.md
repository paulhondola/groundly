# Connecting MCP hosts (Claude Code, Codex) to Groundly

Groundly is an MCP server: `groundly mcp` speaks stdio and is spawned (and
killed) by the host — no daemon to manage. It works from any working
directory (subjects live under `~/.groundly/`).

**Prerequisites:** Groundly installed so `groundly` is on your `PATH`, and at
least one indexed subject (`groundly init <SUBJECT> && groundly index
<SUBJECT> <files...>`). No API key is needed for `search`/`get_page`; `ask`
needs a configured chat provider — see [lm-studio.md](lm-studio.md) for the
local zero-key option.

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

Verify with `/mcp` inside Claude Code — `groundly` should list four tools.

## Codex

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.groundly]
command = "groundly"
args = ["mcp"]
```

(Newer Codex CLI versions also accept `codex mcp add groundly -- groundly
mcp`.)

## HTTP transport (`groundly serve`)

For hosts that connect to a URL instead of spawning a stdio subprocess, run
the same server over Streamable HTTP:

```sh
groundly serve            # binds http://127.0.0.1:8000/mcp
groundly serve --port 9000
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

| Tool | What it does | Needs a provider? |
|---|---|---|
| `list_subjects` | subjects with material/page/chunk counts | no |
| `search` | top-k ranked chunks; the host composes the answer (grounding not enforced) | no |
| `ask` | enforced grounded answer with citations; refuses when the materials don't cover it | yes (chat) |
| `get_page` | verbatim chunks for one page of one material — opens what a citation points to | no |

Citations carry URIs like `groundly://<subject>/<file>#page=N`; the same
document is readable as an MCP resource (`groundly://<subject>/<file>`,
chunks grouped by page). Everything stays local: the only network traffic is
your own configured provider.

Try it:

> Using groundly, what subjects do I have indexed?
>
> Ask groundly's OS subject what causes a deadlock, then open the page it cites.

If the host reports the server as failed to start, run `groundly mcp` in a
terminal yourself — import/config errors print there. (It waiting silently on
stdin means it's healthy; Ctrl-C out.)
