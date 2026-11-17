# `groundly start`: page and MCP on one address — design

Date: 2026-11-17 · Size: small change · Records as decision 45 (via `/decision`).

## Intent

Several Claude Code sessions often work on different subjects at once. Over stdio each session spawns its own Groundly and loads bge-m3 + the reranker separately; over HTTP they share one load. So the student's daily setup is HTTP, and it should be one command at one memorable address instead of two processes on two ports.

## Goals

- `groundly start [--port N]` (default **8765**) runs one process on 127.0.0.1 serving:
  - `http://groundly.localhost:8765/` — the topic-tree and test page (what `serve` shows), opened in the browser;
  - `http://127.0.0.1:8765/mcp` — the MCP tools over Streamable HTTP, for every host session (also answers on `groundly.localhost`; MCP clients are given the IP because they resolve through the OS — changed after the security review).
- `groundly.localhost` is accepted alongside `127.0.0.1` and `localhost`; existing URLs keep working.

## Non-goals

- No port-free URL (port 80 needs root on loopback; a proxy daemon is out of scope).
- No `.local` name (mDNS/Bonjour collision, lookup stalls).
- No user-chosen hostname: the Host allowlist is the DNS-rebinding guard, so names are a fixed list.
- `serve` and `mcp --http` stay as they are (page-only, and MCP-only on 8000) — `start` adds, removes nothing.

## Anti-goals

- Anything that binds beyond 127.0.0.1, or weakens the Host/Origin checks to make a name work.
- The page's own routes loading a model or writing to `store.db`. In the `start` process models load lazily on the first `/mcp` call, never because the page was opened.

## Approaches

| | Shape | Trade-off |
|---|---|---|
| **A — one name, split by path (chosen)** | `groundly.localhost:8765/` page, `/mcp` tools | One name in the allowlist, smallest change, one address to remember |
| B — subdomains | `dashboard.` / `mcp.groundly.localhost:8765` | Kiri-like look; more routing code, two names to allow |
| C — no port | `groundly.localhost` on :80 | Needs root or a background proxy; dropped |

Verified on macOS: `*.localhost` resolves to 127.0.0.1/::1 at the OS level and in Node, so both the browser and Claude Code's MCP client reach it with no setup; binding 127.0.0.1:80 unprivileged is refused.

## Decided

- Verb `start`, HTTP transport, path split, default port 8765.
- `serve` also prints the `groundly.localhost` URL.
- Host connect line in the guide: `claude mcp add --transport http groundly http://127.0.0.1:8765/mcp`.

## Left to the builder

- Mounting FastMCP's `http_app()` under the page's Starlette app, including passing its lifespan to the parent.
- Making FastMCP's own host/origin protection accept `groundly.localhost` (it must stay on).
- Keeping the page's CSP and same-Origin write check scoped to page routes so `/mcp` is unaffected, and `TrustedHost` covering both.
- Busy-port handling and browser opening shared with `serve`.
- Tests: Host header `groundly.localhost` accepted, a foreign Host refused, on both `/` and `/mcp`; the surface test unchanged (no new tool).
- Docs in the same change: decision 45, conventions CLI verb list, security.md (one process, both surfaces), overview process table, mcp-hosts guide.
