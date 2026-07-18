"""Groundly CLI — batch lifecycle verbs; the host agent is the interactive surface.

Command surface per docs/superpowers/specs/2026-07-16-p1-cli-surface-design.md.
Later phases add verbs: P2 import/export · P3 ask · P4 mcp/serve · P6 export-deck.
"""

from groundly.cli import ask, mcp, models, sharing, subjects  # noqa: F401  registers verbs on `app`
from groundly.cli.app import app

__all__ = ["app"]
