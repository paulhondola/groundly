# Distribution

There is no deployment — Groundly runs on the student's machine. This doc covers how it gets there and what it costs in disk/RAM. (The archived multi-tenant iteration had a deployment doc; this replaces it.)

## Install

```
uv tool install groundly
```

Install [`uv`](https://docs.astral.sh/uv/) first if absent. Python ≥3.11 is provisioned by uv itself.

**Honest footprint** (documented trade-off, decided): the dependency tree includes torch; first `index` run downloads bge-m3 (~2.2GB) and, on first query, bge-reranker (~0.5GB) into the Hugging Face cache. This is the price of local, pinned, quality-first embeddings — Groundly cannot match a single-binary tool's footprint and does not pretend to. Models load lazily (never at MCP spawn). Run `groundly models install` to fetch both models eagerly instead — useful before going offline or before wiring up an MCP host, so the first real tool call doesn't pay the download cost. `groundly models` shows whether each is cached; `groundly models uninstall` reclaims the disk space by removing both from the cache again.

## Host wiring (MCP)

```jsonc
// Claude Code / Desktop / Codex config
{ "mcpServers": { "groundly": { "command": "groundly", "args": ["mcp"] } } }
```

Connect your host first, then install the skills that map a course, write questions and grade (`groundly skill install`, which installs into `~/.agents/skills/` and any present host among `~/.claude/skills/`, `~/.codex/skills/` and `~/.gemini/config/skills/`; see [`../guides/mcp-hosts.md`](../guides/mcp-hosts.md)). `groundly serve` shows the tree on loopback.

stdio servers are spawned and killed by the host — there is no daemon to manage. Students running several host sessions run `groundly start`, so the models load once and the page is served alongside (decisions 45, 54). Hosts stay registered over stdio and share that load: while `start` runs, each `groundly mcp` forwards the tools that need the models to it, and serves them itself when `start` is not running (decision 54). A host that can only connect by URL points at `http://127.0.0.1:8765/mcp` (streamable HTTP, `127.0.0.1` only; `groundly start --http` prints the command) and needs `start` running.

## Requirements

- macOS / Linux / Windows; ~6GB disk (deps + models); 8GB RAM comfortable (indexing peaks with Docling + embedding in memory; files are processed one at a time, in one extraction worker per run, which bounds it).
- CPU-only works: indexing is one-time per subject (minutes); queries are milliseconds (retrieval) + rerank (~1s CPU). Apple Silicon/GPU accelerates both.
- **Apple Silicon fast path** (decision 48): on macOS 14 or later on arm64, `mlx` installs and bge-m3 and the reranker run on MLX; long-running processes (`start`, `mcp`, `serve`) never import torch. Older macOS on arm64, Intel Macs, Linux and Windows run the same models on torch through FlagEmbedding. On first use the bge-m3 weights are converted once to f16 safetensors (~1.1 GB) under `~/.groundly/models/`, in a child process; `groundly models uninstall` removes them with the cached models.
- No API key is needed or accepted anywhere ([`cost-model.md`](cost-model.md)).

## Release process

- PyPI package `groundly`; versions are semver; the manifest records `tool_version` on every export.
- CI (GitHub Actions): ruff + pyrefly + pytest on Linux/macOS; no service containers — the test suite runs on SQLite files and stub embedders.
