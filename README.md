# Groundly

**Study your course with the AI you already pay for.** Index your course materials once; then any MCP-capable agent (Claude Code, Claude Desktop, Codex) searches the actual course content with page-level citations. A topic map of the course is available now, and verified quizzes with mastery tracking are on the way. Everything runs on your machine, and nothing needs an API key.

Bachelor thesis project, Universitatea Politehnica Timișoara.

```
$ groundly index <SUBJECT> ./slides/
  ✓ 12 PDFs → 384 chunks → embedded (bge-m3, local) → indexed
$ claude mcp add groundly -- groundly mcp   # wire into Claude Code
$ groundly skill install                     # then: "map my course"
```

Then, inside your agent: *"What did lecture 4 say about deadlock prevention?"* It searches the course's own slides and answers citing **lecture-04.pdf, page 12, "Deadlocks › Prevention"**.

## How it works

**index → search → topic tree → verified quizzes → progress.** Groundly holds the course and checks the work; your agent does the thinking. A study session stays in your agent and Groundly's local page. Nothing sends you off to another app.

- **Grounded search** (available now): bge-m3 dense + learned sparse + BM25, cross-encoder reranked, via MCP or `groundly search`. Every chunk carries a `groundly://<subject>/<file>#page=N` URI; `get_page` opens the exact page.
- **Sharing** (available now): `groundly export <SUBJECT>` writes one file; a coursemate imports it and their agent uses it directly. One person pays the indexing cost.
- **Topic tree** (available now, Claude Code): a roadmap-style map of each subject. Your agent writes it from the course's own structure, Groundly picks each node's slides and rejects any topic the course does not cover, and `groundly serve` shows it on a local page. It is read-only for now: progress arrives with quizzes.
- **Verified quizzes** (planned): your agent writes the questions, and Groundly re-retrieves every one and rejects any whose cited slides don't support it. Your answers build mastery per topic.

## Install

```
uv tool install groundly
```

The first run downloads the embedding models (~2.7GB total). No API key, no account, no upload.

**Setup guide:** [connect Claude Code / Codex to the MCP server](docs/guides/mcp-hosts.md)
