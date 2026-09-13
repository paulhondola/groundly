"""Module-boundary invariant no other test would notice breaking
(.claude/rules/architecture.md): clients (`cli/`, `mcp/`) -> services (`agents/`,
`retrieval/`, `ingestion/`) -> foundations (`llm/`, `core/`), and nothing imports the
client layer. Checked by reading imports rather than by importing, so a violation is
reported as a named boundary breach instead of an ImportError."""

import ast
from pathlib import Path

_PACKAGE = Path(__file__).resolve().parents[1] / "groundly"
_CLIENTS = ("cli", "mcp")


def _imported_modules(path: Path) -> set[str]:
    """Every `groundly.*` module named by an import in `path`, at any nesting depth —
    function-local imports are how this codebase defers heavy dependencies, so a
    top-level-only scan would miss most of them."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name.startswith("groundly."))
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module.startswith("groundly."):
                found.add(node.module)
    return found


def test_nothing_imports_the_client_layer():
    """A service or foundation reaching back into `cli/` or `mcp/` inverts the stack."""
    offenders: dict[str, list[str]] = {}
    for path in sorted(_PACKAGE.rglob("*.py")):
        rel = path.relative_to(_PACKAGE).as_posix()
        if rel.split("/")[0] in _CLIENTS:
            continue  # a client may import its own layer
        bad = sorted(
            m for m in _imported_modules(path) if m.split(".")[1:2] and m.split(".")[1] in _CLIENTS
        )
        if bad:
            offenders[rel] = bad
    assert not offenders, f"non-client modules importing the client layer: {offenders}"
