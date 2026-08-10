"""Module-boundary invariants that no other test would notice breaking.

`.claude/rules/architecture.md`: layers run clients (`cli/`, `mcp/`, `web/`, `eval/`) ->
services (`agents/`, `retrieval/`, `ingestion/`) -> foundations (`llm/`, `core/`), and
`eval/` is a *client*, not a service — a research surface driving the arms offline
(decision 27).

Checked by reading imports rather than by importing, so a violation is reported as a
named boundary breach instead of an ImportError in whichever test ran first.
"""

import ast
from pathlib import Path

_PACKAGE = Path(__file__).resolve().parents[1] / "groundly"


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


def test_eval_does_not_import_the_agents_layer():
    """`groundly eval` scores retrieval without paying for generation, so it has no
    business reaching the ask pipeline. It did until the arm table moved to
    `retrieval/arms.py`: importing `ARMS` from `agents/ask.py` dragged `llm/chat`,
    `agents/prompts` and `agents/citations` into a harness that calls none of them."""
    offenders = {
        path.relative_to(_PACKAGE).as_posix(): sorted(
            m for m in _imported_modules(path) if m.startswith("groundly.agents")
        )
        for path in sorted((_PACKAGE / "eval").rglob("*.py"))
    }
    offenders = {k: v for k, v in offenders.items() if v}
    assert not offenders, f"eval/ is a client of retrieval, not of agents: {offenders}"


def test_nothing_imports_the_client_layer():
    """ "Dependencies point one way; nothing imports the client layer." A service or
    foundation reaching back into `cli/`, `mcp/`, `web/` or `eval/` inverts the whole
    stack — and `eval/` in particular must stay offline-only, with no runtime path
    depending on it (decision 27)."""
    clients = ("cli", "mcp", "web", "eval")
    offenders: dict[str, list[str]] = {}
    for path in sorted(_PACKAGE.rglob("*.py")):
        rel = path.relative_to(_PACKAGE).as_posix()
        if rel.split("/")[0] in clients:
            continue  # a client may import its own layer
        bad = sorted(
            m for m in _imported_modules(path) if m.split(".")[1:2] and m.split(".")[1] in clients
        )
        if bad:
            offenders[rel] = bad
    assert not offenders, f"non-client modules importing the client layer: {offenders}"
