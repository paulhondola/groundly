# Groundly README Refresh Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan. Steps use checkboxes for tracking.

**Goal** — Update the README to describe the current Groundly study workflow and link to package, CI, license, and stack information.

**Architecture** — `README.md` is the entry point on GitHub and PyPI. Link to the existing documentation for interface details.

**Tech stack** — GitHub Flavored Markdown and Shields.io.

**Spec** — [Groundly README refresh](../specs/2026-11-25-readme-refresh-design.md).

## Global constraints

- Document the published features and their prerequisites.
- Keep the scope to documentation and preserve the thesis attribution.

## Review focus

- Match the CLI arguments and order the steps so the reader can cite sources and then map the course.
- Check that the page buttons require Claude Code and that remote pairing leaves MCP on loopback.
- Describe what the topic and question checks establish, what the AI host does, and what an export contains.
- Confirm that badge URLs return the expected SVG labels and that links work from the published README.

## Task 1 — Rewrite and verify the README

- [x] Replace `README.md` with the student walkthrough, feature overview, remote pairing and sharing examples, and documentation links.
- [x] Check sample commands against CLI help and the host guide. Fetch all badge URLs and validate the destinations.
- [x] Run `git diff --check` and commit the documentation on a feature branch.
