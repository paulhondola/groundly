"""Groundly CLI — batch lifecycle verbs; the host agent is the interactive surface.

Command surface per docs/superpowers/specs/2026-07-16-p1-cli-surface-design.md.
Later phases add verbs: P2 import/export · P3 ask · P4 mcp/serve · P6 export-deck.
"""

from groundly.cli import (  # noqa: F401  registers verbs on `app`
    ask,
    decks,
    mcp,
    models,
    serve,
    sharing,
    subjects,
)
from groundly.cli.app import app

__all__ = ["app"]
