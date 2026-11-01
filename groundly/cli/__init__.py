"""Groundly CLI — batch lifecycle verbs; the host agent is the interactive surface."""

from groundly.cli import (  # noqa: F401  registers verbs on `app`
    mcp,
    models,
    search,
    serve,
    sharing,
    skill,
    subjects,
)
from groundly.cli.app import app

__all__ = ["app"]
