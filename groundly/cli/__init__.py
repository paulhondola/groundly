"""Groundly CLI — batch lifecycle verbs; the host agent is the interactive surface.

Command surface per docs/superpowers/specs/2026-07-16-p1-cli-surface-design.md.
Later phases add verbs: P2 import/export · P3 ask · P4 mcp/serve · P6 export-deck ·
export-graph (graph visualization, decision 26) · eval (retrieval eval harness,
decision 27 — a research verb, not a student-facing one) · eval-grounding (the
enforced-vs-host grounding-fidelity comparison, decision 30 — likewise research-only).
"""

from groundly.cli import (  # noqa: F401  registers verbs on `app`
    ask,
    decks,
    eval,
    eval_grounding,
    graph,
    mcp,
    models,
    serve,
    sharing,
    subjects,
)
from groundly.cli.app import app

__all__ = ["app"]
