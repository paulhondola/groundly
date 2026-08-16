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


# The retrieval sweep: `groundly eval --arms ...`, which scores candidates without paying
# for generation. Named explicitly rather than "everything under eval/" because the
# package now holds a second slice that legitimately does pay for generation — see below.
#
# `__init__.py` is in the list and must stay: it executes on every `import
# groundly.eval.runner`, so an `agents` import placed there re-breaks the
# generation-free property from a file nobody thinks of as part of the sweep.
_RETRIEVAL_ONLY = ("__init__.py", "runner.py", "metrics.py", "gold.py")


def test_the_retrieval_sweep_does_not_import_the_agents_layer():
    """`groundly eval` scores retrieval without paying for generation, so it has no
    business reaching the ask pipeline. It did until the arm table moved to
    `retrieval/arms.py`: importing `ARMS` from `agents/ask.py` dragged `llm/chat`,
    `agents/prompts` and `agents/citations` into a harness that calls none of them.

    **Scoped to the retrieval modules, deliberately.** This used to cover every file under
    `eval/`, which read as a layer rule but was not one: `.claude/rules/architecture.md`
    puts `eval/` in the client layer and `agents/` in the service layer, and clients may
    import services. What it actually protected was the *retrieval-only* property of the
    sweep, and that is still worth pinning — a graph-arm sweep runs for hours and must not
    start loading a chat client it never calls.

    `eval/grounding.py` is the generation slice decision 29 anticipated ("generation-side
    metrics … come from full `ask()` runs"), and it calls the real pipeline rather than a
    copy, so the measured pipeline is the shipped one. It is therefore exempt, and the
    exemption is a named list rather than a silent widening: a *new* file under `eval/`
    reaching into `agents/` still has to justify itself here."""
    offenders = {
        path.name: sorted(m for m in _imported_modules(path) if m.startswith("groundly.agents"))
        for path in sorted((_PACKAGE / "eval").glob("*.py"))
        if path.name in _RETRIEVAL_ONLY
    }
    offenders = {k: v for k, v in offenders.items() if v}
    assert not offenders, (
        f"the retrieval sweep is a client of retrieval, not of agents: {offenders}"
    )


def test_the_retrieval_only_modules_still_exist():
    """A guard on the guard above: renaming `runner.py` would make its exemption list
    match nothing and the test would pass vacuously, silently retiring the protection."""
    present = {p.name for p in (_PACKAGE / "eval").glob("*.py")}
    assert set(_RETRIEVAL_ONLY) <= present, f"missing: {set(_RETRIEVAL_ONLY) - present}"


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
