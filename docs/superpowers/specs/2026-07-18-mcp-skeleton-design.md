# MCP Skeleton (P4 v1) — Design

Date: 2026-07-18 · Branch: `mcp` · Approved by Paul

## Goal

P4 v1 per spec §8: FastMCP server exposing `list_subjects`, `search`, `ask`,
`get_page` over **stdio** (`groundly mcp`, host-spawned), plus citation
resources `groundly://<subject>/<file>#page=N`. (`groundly serve` — the same
tool surface over Streamable HTTP on 127.0.0.1 — shipped later on branch
`http-streaming`; see docs/guides/mcp-hosts.md.)
Gate: demo inside Claude Code — search, ask, open a cited page.

## Architecture

One module, thin wrappers. Everything wraps an existing service function;
the CLI (`cli/ask.py`) already demonstrates the pattern.

- `groundly/mcp/server.py` — module-level `mcp = FastMCP("groundly")`, the
  4 tools, the resource template. **No heavy imports at module top**: service
  imports live inside tool bodies so host spawn → handshake is fast and
  bge-m3 loads on first `search` (lazy-loading invariant).
- `groundly/cli/` — new `groundly mcp` verb that imports the server and
  calls `mcp.run()` (stdio). No daemon.
- Layering: `mcp/` → `agents`/`retrieval`/`core` only. Nothing imports `mcp/`.
- One new store method: `SQLiteSubjectStore.page_chunks(filename, page)` —
  chunks joined to materials for one page, in chunk-id order.

## Tools

| Tool | Wraps | Returns (structured) |
|---|---|---|
| `list_subjects()` | `discover_subjects()` + manifest/material counts | `[{subject, materials, pages, chunks, graph_built}]` |
| `search(subject, query, k=8)` | `retrieval.vector.search()` | `[{chunk_id, text, score, filename, page, heading_path, uri}]` |
| `ask(subject, query)` | `agents.ask.ask()` | `{answer, citations: [{chunk_id, filename, page, heading_path, uri}]}` |
| `get_page(subject, filename, page)` | new `page_chunks()` | verbatim chunk texts for that page, ordered, with heading paths |

`uri` = `groundly://<subject>/<filename>#page=N`.

Tool docstrings are host-model UX (conventions rule): `ask` = "enforced
grounded answer with citations; refuses when not covered"; `search` = "raw
ranked chunks, you compose — grounding not enforced". Errors are specific:
`ProviderNotConfiguredError` → "ask needs a configured chat provider; search
works without one"; `NoCitationsError` → the refusal semantics; unknown
subject → names it and says how to list subjects.

## Citation resources

Resource template `groundly://{subject}/{filename}` returning the document's
chunks grouped by page. The `#page=N` fragment locates the page. Open
implementation question: if FastMCP passes fragments through to the handler,
serve just that page; if not, the fragment stays a client-side locator and
`get_page` is the precise path (the gate demo uses `get_page` anyway).
Citations resolve to **verbatim chunks only** — never summaries, never raw
file bytes (grounding rule).

## Testing

pytest, existing stub-embedder/stub-provider pattern, FastMCP in-memory
client against a temp subject fixture:

- per tool: happy path, unknown subject
- zero-key: `search` works fully, `ask` fails with the specific message
- UC-02 equivalence: MCP `ask` ≡ CLI `ask` (same function, same result)
- spawn-speed guard: importing `groundly.mcp.server` must not pull in
  sentence-transformers/torch (assert on `sys.modules`)
