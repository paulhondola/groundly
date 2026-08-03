"""Gold-set loading and validation.

Questions are labelled by **(filename, page)**, never by chunk id: chunk ids are SQLite
rowids that shift on every re-index, while a filename and a page survive one. Labels
resolve against the live store at run time (`resolve`).

The load-bearing validation is `source_file`: the exam and quiz files a question was
drawn from are *themselves indexed*, so a question lifted from `Examen.md` will retrieve
`Examen.md`. That "hit" is the question, not the answer, and would silently inflate every
number the thesis reports. A gold row whose `expected` points back at its own
`source_file` is rejected at load time, and the runner separately counts how often an arm
retrieves from it anyway (`metrics.leakage`).
"""

import json
from dataclasses import dataclass
from pathlib import Path

LANGS = ("en", "ro")
CLASSES = ("factoid", "multi-hop", "global")

_REQUIRED = ("id", "query", "lang", "class", "expected")


class GoldSetError(ValueError):
    """A gold file that cannot be trusted to produce honest numbers. The message names
    the offending line and what is wrong with it — never a bare parse error."""


@dataclass(frozen=True)
class Expected:
    """A labelled answer location. `page is None` matches the whole file — correct for
    markdown and source files, which have no pages."""

    file: str
    page: int | None


@dataclass(frozen=True)
class GoldQuestion:
    id: str
    query: str
    lang: str
    klass: str  # `class` is a keyword; the JSONL field is still "class"
    expected: tuple[Expected, ...]
    source_file: str | None
    notes: str | None


def _question(raw: dict, lineno: int) -> GoldQuestion:
    missing = [f for f in _REQUIRED if f not in raw]
    if missing:
        raise GoldSetError(f"line {lineno}: missing required field(s): {', '.join(missing)}")

    qid, lang, klass = raw["id"], raw["lang"], raw["class"]
    if lang not in LANGS:
        raise GoldSetError(f"line {lineno} ({qid}): lang {lang!r} is not one of {', '.join(LANGS)}")
    if klass not in CLASSES:
        raise GoldSetError(
            f"line {lineno} ({qid}): class {klass!r} is not one of {', '.join(CLASSES)}"
        )
    if not raw["expected"]:
        raise GoldSetError(f"line {lineno} ({qid}): expected is empty — nothing to score against")

    source_file = raw.get("source_file")
    expected = []
    for item in raw["expected"]:
        if "file" not in item:
            raise GoldSetError(f"line {lineno} ({qid}): an expected entry has no 'file'")
        if source_file is not None and item["file"] == source_file:
            raise GoldSetError(
                f"line {lineno} ({qid}): expected points at {source_file!r}, the question's own "
                "source file — that scores retrieving the question, not the answer. Label the "
                "lecture material that answers it instead."
            )
        expected.append(Expected(file=item["file"], page=item.get("page")))

    return GoldQuestion(
        id=qid,
        query=raw["query"],
        lang=lang,
        klass=klass,
        expected=tuple(expected),
        source_file=source_file,
        notes=raw.get("notes"),
    )


def load(path: Path) -> list[GoldQuestion]:
    """Parse and validate a gold JSONL file. Blank lines and `#` comments are skipped."""
    if not path.exists():
        raise GoldSetError(f"no gold set at {path}")

    questions: list[GoldQuestion] = []
    seen: set[str] = set()
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise GoldSetError(f"line {lineno}: not valid JSON — {exc.msg}") from exc
        question = _question(raw, lineno)
        if question.id in seen:
            raise GoldSetError(f"line {lineno}: duplicate question id {question.id!r}")
        seen.add(question.id)
        questions.append(question)

    if not questions:
        raise GoldSetError(f"{path} contains no questions")
    return questions


def _index(chunks) -> tuple[dict[tuple[str, int | None], set[int]], dict[str, set[int]]]:
    by_page: dict[tuple[str, int | None], set[int]] = {}
    by_file: dict[str, set[int]] = {}
    for row in chunks:
        by_page.setdefault((row["filename"], row["page"]), set()).add(row["chunk_id"])
        by_file.setdefault(row["filename"], set()).add(row["chunk_id"])
    return by_page, by_file


def resolve(
    questions: list[GoldQuestion], store
) -> tuple[dict[str, set[int]], dict[str, set[int]], list[str]]:
    """Resolve labels against the live store in one pass over `all_chunks()`.

    Returns (expected chunk ids per question, source-file chunk ids per question,
    warnings). A label that resolves to nothing is a warning naming the label, not a
    crash — a partly-stale gold set should still produce numbers for the rows that
    are fine, with the bad rows called out.
    """
    by_page, by_file = _index(store.all_chunks())

    expected: dict[str, set[int]] = {}
    source: dict[str, set[int]] = {}
    warnings: list[str] = []

    for q in questions:
        hits: set[int] = set()
        for want in q.expected:
            found = (
                by_file.get(want.file, set())
                if want.page is None
                else by_page.get((want.file, want.page), set())
            )
            if not found:
                where = want.file if want.page is None else f"{want.file} p.{want.page}"
                warnings.append(f"{q.id}: expected {where} matches no chunk in this subject")
            hits |= found
        expected[q.id] = hits
        source[q.id] = by_file.get(q.source_file, set()) if q.source_file else set()

    return expected, source, warnings
