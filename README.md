# Groundly

[![PyPI version](https://img.shields.io/pypi/v/groundly?logo=pypi)](https://pypi.org/project/groundly/)
[![Python 3.11 through 3.14](https://img.shields.io/badge/Python-3.11%E2%80%933.14-3776AB?logo=python&logoColor=white)](https://github.com/paulhondola/groundly/blob/main/pyproject.toml)
[![Pull request checks](https://img.shields.io/github/actions/workflow/status/paulhondola/groundly/ci.yml?event=pull_request&label=PR%20checks)](https://github.com/paulhondola/groundly/actions/workflows/ci.yml)
[![MIT license](https://img.shields.io/github/license/paulhondola/groundly)](https://github.com/paulhondola/groundly/blob/main/LICENSE)

[![FastMCP](https://img.shields.io/badge/FastMCP-2D3748)](https://gofastmcp.com/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Svelte 5](https://img.shields.io/badge/Svelte-5-FF3E00?logo=svelte&logoColor=white)](https://svelte.dev/)
[![Docling](https://img.shields.io/badge/Docling-3058A3)](https://docling-project.github.io/docling/)
[![BGE-M3](https://img.shields.io/badge/BGE--M3-6B4DCB)](https://huggingface.co/BAAI/bge-m3)
[![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)](https://sqlite.org/)

**Turn your course materials into a searchable study guide with page-level citations.** Index your lectures, slides, and notes on your computer, then use an MCP host such as Claude Code or Codex to ask about the course, build a topic map, and write practice questions. Take tests in your browser, track your mastery, or share the course as one `.groundly` file.

## Quick start

**1. Install Groundly and index a course.** Install [uv](https://docs.astral.sh/uv/) first. In this example, `os` is the subject name and `./course-materials/` is your folder.

```sh
uv tool install groundly
groundly index os ./course-materials/
```

The local embedding and reranking models download on first use (about 2.7 GB together). Run `groundly models install` to fetch both ahead of time.

**2. Connect your AI host and install the study skills.** Here’s the setup for Claude Code. The [MCP host guide](https://github.com/paulhondola/groundly/blob/main/docs/guides/mcp-hosts.md) covers Codex, Antigravity, and HTTP setup.

```sh
claude mcp add --scope user groundly -- groundly mcp
groundly skill install
```

Open a fresh host session and ask *“Use Groundly to explain deadlock prevention in my `os` course. Cite the lecture and page.”* Then ask *“Map my `os` course”* and *“Write ten questions on deadlocks.”*

**3. Open the study page.** Pick a topic or the whole course and start a test from the question bank.

```sh
groundly serve
```

## What you can do

| Capability | How it works |
| --- | --- |
| **Index and maintain a course** | Ingest PDFs, Word files, slides, spreadsheets, EPUBs, Jupyter notebooks, and plain text, including Markdown and source code. Local OCR handles scanned PDFs and images. Re-run `groundly index` to add new material and skip duplicates. Use `groundly list`, `groundly title`, and `groundly remove` to manage subjects. |
| **Search with citations** | Search from your MCP host or with `groundly search`. Groundly combines dense and sparse BGE-M3 retrieval with BM25 and reranking, including Romanian queries over English sources. Hits include verbatim text, the filename, page where available, and heading path. The host can open the cited page with `get_page`. |
| **Browse a course map** | The host drafts a two-level topic tree from the course outline. Groundly selects sources for each topic and requires relevant course material before saving the tree. Browse topics, cited passages, and available source PDFs in the study page. |
| **Take practice tests** | Draw 5, 10, or 20 questions for a subtopic, a topic, or the whole course. The draw prioritizes unanswered and previously missed questions. The bank supports single-select, multiple-select, code-reading, and written questions. |
| **Review results and track mastery** | Choice questions are scored on submission. Ask your host to grade written answers against the references and cited material. Review past attempts, feedback, and source citations. Mastery requires at least 10 graded answers, with 9 or more correct among the latest 10. |
| **Map, write, and grade from the browser** | With a signed-in Claude Code CLI on the Groundly machine (macOS or Linux), **Create topic map**, **Write questions**, and **Grade now** start a run with live updates. The switcher beside each action shows the provider, model, and effort and remembers your last run settings. |

Groundly saves a question if it is well formed, its citations resolve, and searching with its text returns at least one cited chunk. Your AI host supplies the answer keys and reviews written responses. Groundly itself needs no API key. The index and study history live on your machine, and your AI host may send course excerpts or written answers to its provider when you use it.

## Study from a phone or tablet

Connect the Groundly computer and your mobile device to the same Tailscale network, then start the study page with remote access enabled.

```sh
groundly serve --tailscale
```

Scan the one-time QR code printed in the terminal or shown under **Devices** on the desktop study page. The paired browser can open the map, read cited passages, take tests, and see grading updates. You can revoke devices from the desktop page. MCP stays available only on the Groundly computer.

If you use several MCP sessions, run `groundly start`: it serves the browser app, and while it runs every stdio-registered session sends its searches and submissions to it, so the models load once. Hosts that can only connect by URL use its endpoint at `http://127.0.0.1:8765/mcp` (`groundly start --http` prints that command). It also accepts `--tailscale` for the browser app.

## Share a course

```sh
groundly export os                # creates os.groundly
groundly import os.groundly        # run on the recipient's computer
```

The bundle includes the searchable index, topic map, question bank, and original materials by default. When the pinned embedding configuration matches, the recipient can reuse the index immediately. Personal attempts, written answers, mastery, and paired device sessions stay private. `--no-materials` omits the original files and still includes searchable excerpts.

## Documentation

- [MCP host setup and study skills](https://github.com/paulhondola/groundly/blob/main/docs/guides/mcp-hosts.md)
- [Project specification](https://github.com/paulhondola/groundly/blob/main/docs/groundly-spec.md) and [architecture overview](https://github.com/paulhondola/groundly/blob/main/docs/architecture/overview.md)
- [System requirements and local inference](https://github.com/paulhondola/groundly/blob/main/docs/infrastructure/distribution.md), including MLX on Apple Silicon
- [Security, privacy, and Tailscale](https://github.com/paulhondola/groundly/blob/main/docs/infrastructure/security.md)

Bachelor thesis project at Universitatea Politehnica Timișoara.
