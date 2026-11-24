"""Offline OpenAPI schema exporter and drift checker for Groundly's /api/* routes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from groundly.web.app import create_app

_DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / "web" / "openapi.json"


def export_openapi() -> dict[str, Any]:
    """Build the FastAPI OpenAPI schema for Groundly's /api/* routes without starting a server."""
    return create_app().openapi()


def format_openapi(spec: dict[str, Any]) -> str:
    """Serialize an OpenAPI schema deterministically for committed diffing."""
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export or verify Groundly's OpenAPI schema.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit with status 1 if web/openapi.json is missing or differs from the app schema.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help="Destination path for the exported OpenAPI JSON.",
    )
    args = parser.parse_args(argv)
    rendered = format_openapi(export_openapi())
    output: Path = args.output
    if args.check:
        if not output.exists() or output.read_text(encoding="utf-8") != rendered:
            print(
                f"{output} is out of date; run: uv run python -m groundly.web.openapi",
                file=sys.stderr,
            )
            return 1
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
