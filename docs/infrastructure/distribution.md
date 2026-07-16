# Distribution

There is no deployment — Groundly runs on the student's machine. This doc covers how it gets there and what it costs in disk/RAM. (The archived multi-tenant iteration had a deployment doc; this replaces it.)

## Install

```
curl -fsSL https://groundly.ai/install.sh | sh
```

The script does two things: install `uv` if absent, then `uv tool install groundly`. Equivalent by hand: `uv tool install groundly`. Python ≥3.11 is provisioned by uv itself.

**Honest footprint** (documented trade-off, decided): the dependency tree includes torch; first `index` run downloads bge-m3 (~2.2GB) and, on first query, bge-reranker (~0.5GB) into the Hugging Face cache. This is the price of local, pinned, quality-first embeddings — Groundly cannot match a single-binary tool's footprint and does not pretend to. Models load lazily (never at MCP spawn).

## Host wiring (MCP)

```jsonc
// Claude Code / Desktop / Codex config
{ "mcpServers": { "groundly": { "command": "groundly", "args": ["mcp"] } } }
```

stdio servers are spawned and killed by the host — there is no daemon to manage. Students running multiple hosts point them at one `groundly serve` (streamable HTTP, `127.0.0.1` only) so the models load once.

## Requirements

- macOS / Linux / Windows; ~6GB disk (deps + models); 8GB RAM comfortable (indexing peaks with Docling + embedding in memory; sequential per-file processing bounds it).
- CPU-only works: indexing is one-time per subject (minutes); queries are milliseconds (retrieval) + rerank (~1s CPU). Apple Silicon/GPU accelerates both.
- No API key needed for indexing and search. `ask`, thick generation, and graph builds use the student's configured provider ([`cost-model.md`](cost-model.md)).

## Release process

- PyPI package `groundly`; versions are semver; the manifest records `tool_version` on every export.
- `install.sh` hosted on the groundly.ai domain, pinned to PyPI (no curl-into-bash of moving code beyond the installer itself).
- CI (GitHub Actions): ruff + pytest on Linux/macOS; no service containers — the test suite runs on SQLite files and stub providers.
