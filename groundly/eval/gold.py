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

    # The contamination guard runs corpus-wide, not per row. Labelling ANY question
    # source as the answer is wrong regardless of which row it came from: those files
    # hold questions, so an arm "finding" one has found the prompt, not the material.
    # The per-row check this replaces missed the case where two rows share a question —
    # apd-006 is verbatim in both Examen.md and Quiz 2, and a row sourced from one could
    # legally label the other.
    sources = question_sources(questions)
    for q in questions:
        for want in q.expected:
            if want.file in sources:
                raise GoldSetError(
                    f"{q.id}: expected points at {want.file!r}, which is a question source "
                    "for this gold set — that scores retrieving a question, not the answer. "
                    "Label the lecture material that answers it instead."
                )
    return questions


def question_sources(questions: list[GoldQuestion]) -> frozenset[str]:
    """Every file any question was drawn from. Leakage is measured against this whole
    set for *every* question, including hand-written ones with `source_file: null` —
    retrieving an exam file is retrieving question text whether or not it happens to be
    the file this particular question came from."""
    return frozenset(q.source_file for q in questions if q.source_file)


def _index(chunks) -> tuple[dict[tuple[str, int | None], set[int]], dict[str, set[int]]]:
    by_page: dict[tuple[str, int | None], set[int]] = {}
    by_file: dict[str, set[int]] = {}
    for row in chunks:
        by_page.setdefault((row["filename"], row["page"]), set()).add(row["chunk_id"])
        by_file.setdefault(row["filename"], set()).add(row["chunk_id"])
    return by_page, by_file


def resolve(
    questions: list[GoldQuestion], store
) -> tuple[dict[str, set[int]], dict[str, set[int]], list[str], float]:
    """Resolve labels against the live store in one pass over `all_chunks()`.

    Returns (expected chunk ids per question, question-source chunk ids per question,
    warnings, question-source base rate). A label that resolves to nothing is a warning
    naming the label, not a crash — a partly-stale gold set should still produce numbers
    for the rows that are fine, with the bad rows called out.

    The base rate is the share of the whole corpus that is question-source material
    (apd: 45 of 1,193 = 3.77%). Raw leakage is not readable without it: an arm returning
    95% of the corpus lands at the base rate by construction and so looks *cleaner* than
    a precise arm that genuinely over-retrieves exam text. `leakage / base_rate` is the
    figure that means something — the same set-size confound `retrieved_n` guards for
    hit rate and recall.

    The source set is **the same for every question**: the union of every question
    source in the gold set (`question_sources`). Scoping it per row understated real
    contamination, because a question can appear in more than one indexed file and 16
    of apd's 48 rows declare no source at all — those reported leakage 0.0 by
    construction while still retrieving exam chunks.
    """
    by_page, by_file = _index(store.all_chunks())

    expected: dict[str, set[int]] = {}
    source: dict[str, set[int]] = {}
    warnings: list[str] = []

    source_chunks: set[int] = set()
    for filename in sorted(question_sources(questions)):
        found = by_file.get(filename, set())
        if not found:
            # Silently measuring leakage against a file that is not in the index would
            # report 0.0 and look like a clean result.
            warnings.append(f"source_file {filename} matches no chunk in this subject")
        source_chunks |= found

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
        source[q.id] = source_chunks

    total = sum(len(ids) for ids in by_file.values())
    base_rate = len(source_chunks) / total if total else 0.0
    return expected, source, warnings, base_rate
