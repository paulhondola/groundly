# Groundly

**Local-first course knowledge bases for AI agents.** Index your course materials once; then any MCP-capable agent — Claude Code, Claude Desktop, Codex — searches the actual course content with page-level citations and builds verified flashcards from it. Share the finished knowledge base with your coursemates as a single file.

Bachelor thesis project, Universitatea Politehnica Timișoara.

```
$ groundly index <SUBJECT> ./slides/
  ✓ 12 PDFs → 384 chunks → embedded (bge-m3, local) → indexed
$ # wire into Claude Code:  { "command": "groundly", "args": ["mcp"] }
```

Then, inside your agent: *"What did lecture 4 say about deadlock prevention?"* — it searches the course's own slides and answers citing **lecture-04.pdf, page 12, "Deadlocks › Prevention"**.

## Why not NotebookLM / a Claude Project?

1. **Verified flashcards** — your agent writes the cards; Groundly re-retrieves every one and rejects any whose cited chunks don't support it. Nothing unverified is stored.
2. **Structural citations** — every chunk carries a `groundly://<subject>/<file>#page=N` URI that opens the exact page.
3. **A portable index** — `groundly export <SUBJECT>` → one file; a coursemate imports it and their agent uses it directly. The expensive artifacts (embeddings, knowledge graph, verified decks) are built once and shared.
4. **No API key** — indexing, search, verification and Anki export are local and free. Only the optional knowledge-graph build calls a provider.

## Features

- **Grounded search** — hybrid retrieval (bge-m3 dense + learned sparse + BM25, cross-encoder reranked) via MCP or `groundly search`
- **Verified flashcards → Anki** — your agent generates, Groundly verifies, `.apkg` exports
- **Import/export** — the whole knowledge base as one shareable file, with pinned-model compatibility
- **Knowledge graph (optional)** — `groundly index --graph` extracts entities and communities; `groundly export-graph` renders them as one offline HTML page

## Install

```
uv tool install groundly
```

Local-first honestly stated: the first run downloads the embedding models (~2.7GB total). Everything except the graph build works with **no API key**.

**Setup guides:** [connect Claude Code / Codex to the MCP server](docs/guides/mcp-hosts.md) · [configure the graph-build provider](docs/guides/graphrag-provider.md)

**Thesis:** the measured RAG vs GraphRAG comparison and the enforced-vs-agent-mediated grounding experiment are in [docs/thesis/experiments.md](docs/thesis/experiments.md); the code that produced them is at tag `thesis-experiments-2026-09`.
