"""Attribution extraction — what an answer says it drew on, in every syntax either path
of the grounding-fidelity experiment can produce.

**Both paths are scored by this one extractor, and that is the whole point.** The
enforced path is mandated to emit `[chunk 12]` and `agents/citations.py` raises if none
of them resolve, so its attributions are machine-resolvable by construction. A host
composing from raw `search` results is under no such mandate: it gets `filename`, `page`
and a `groundly://` uri back from the tool and will quote whichever it likes, in prose.
Scoring the two with different extractors — or scoring the host with a `[chunk N]` regex
— produces "the host's citation accuracy is 0.0", which is a statement about the regex
rather than about the host.

So attribution is reported in three layers, and only the third is about correctness:

  present     — is there any attribution at all?
  resolvable  — does it map to a chunk that was actually retrieved?  <- this module
  supported   — does that chunk actually support the claim?          <- eval/judge.py

The **resolvability gap is a finding**, not an error bar. Pure: no store handle, no
provider, no I/O — `ChunkIndex` is built once by the caller and passed in, the same
bargain `eval/metrics.py` makes so the scoring layer is testable without an index.
"""

import re
from dataclasses import dataclass

# The enforced path's mandated marker (agents/prompts.py SYSTEM_RULES). Identical to
# `agents/citations.py`'s pattern on purpose: this module must find exactly what the
# product resolves, or the two disagree about what a citation is.
_CHUNK_RE = re.compile(r"\[chunk (\d+)\]")

# A page reference immediately following a filename. Deliberately narrow — it runs only
# on the characters right after a *known* filename, never as a free scan, so it cannot
# invent an attribution out of a bare number in prose. `pag`/`pagina` are here because
# 9 of apd's 48 gold questions are Romanian and an RO answer cites in Romanian.
_PAGE_RE = re.compile(
    r"""^[\s,;:(\[]*                       # optional separators after the filename
        (?: \#page=                        # the uri form, groundly://subj/file#page=4
          | (?:p|pp|pg|pag|page|pages|pagina|paginile)\.?\s*
        )
        (\d+)""",
    re.IGNORECASE | re.VERBOSE,
)
_PAGE_WINDOW = 24  # chars after a filename searched for the page marker

# A filename is in uri form only when `groundly://<subject>/` ends immediately before it
# (mcp/server.py::_citation_uri). Anchored, not a "is there a uri nearby" search: a prose
# mention of a file one sentence after a uri is a different attribution, and folding the
# two together would misreport which syntax the host actually reaches for — which is the
# resolvability finding itself.
_URI_BEFORE_RE = re.compile(r"groundly://[^/\s]+/$")


@dataclass(frozen=True)
class Attribution:
    """One thing an answer claims to be drawing on.

    `resolved` is empty for an attribution that names something real but retrieves
    nothing — a page that is not in the index, or a file the arm never returned. That is
    a *measurement*, not a failure: an unresolvable attribution is exactly what the
    resolvability layer exists to count.
    """

    raw: str
    kind: str  # "chunk" | "uri" | "prose"
    resolved: frozenset[int]
    span: tuple[int, int]

    @property
    def key(self) -> tuple:
        """Identity for deduplication. An answer that cites the same page four times has
        made one attribution, not four — otherwise a repetitive answer scores its
        resolvability rate four times over."""
        return (self.kind, self.raw.strip().casefold())


class ChunkIndex:
    """`(filename, page)` and `filename` -> chunk ids, plus the corpus's filename list.

    Built from `store.all_chunks()` — the same one-pass shape `eval/gold.py::resolve`
    uses to turn `(filename, page)` gold labels into chunk ids. Attribution resolution is
    that identical problem arriving from the other direction, so it gets the identical
    lookup rather than a second one that can drift.
    """

    def __init__(self, rows) -> None:
        self.by_page: dict[tuple[str, int | None], set[int]] = {}
        self.by_file: dict[str, set[int]] = {}
        for row in rows:
            self.by_page.setdefault((row["filename"], row["page"]), set()).add(row["chunk_id"])
            self.by_file.setdefault(row["filename"], set()).add(row["chunk_id"])
        # Longest first so "Curs 3.pdf" is matched before a hypothetical "Curs 3" — a
        # shorter filename that prefixes a longer one would otherwise swallow it and
        # resolve to the wrong file.
        self.filenames = sorted(self.by_file, key=len, reverse=True)
        self.chunk_ids = {cid for ids in self.by_file.values() for cid in ids}
        self._folded = {name.casefold(): name for name in self.by_file}

    def resolve(self, filename: str, page: int | None) -> frozenset[int]:
        """Chunk ids for a `(filename, page)` attribution; empty if it matches nothing.

        Case-insensitive on the filename because a host retypes what the tool handed it
        and may not preserve case. Deliberately **not** fuzzy beyond that: a near-miss
        match would silently credit the host for citing a file it did not name, which
        inflates exactly the number this experiment is trying to measure honestly.
        """
        actual = self._folded.get(filename.casefold())
        if actual is None:
            return frozenset()
        if page is None:
            return frozenset(self.by_file[actual])
        return frozenset(self.by_page.get((actual, page), set()))


def _page_after(text: str, pos: int) -> tuple[int | None, int]:
    """Page number following `pos`, and where the reference ends. `(None, pos)` if there
    is none — a bare filename is a whole-file attribution, which is legitimate for the
    markdown and source files in the corpus that have no pages at all."""
    match = _PAGE_RE.match(text[pos : pos + _PAGE_WINDOW])
    if match is None:
        return None, pos
    return int(match.group(1)), pos + match.end()


def extract(text: str, index: ChunkIndex) -> list[Attribution]:
    """Every distinct attribution in `text`, resolved against the corpus.

    Filenames are found by scanning for the corpus's *known* filenames as literals
    rather than by a regex guessing at filename shape. Two reasons, both load-bearing:
    filenames here contain spaces (`Quiz 2`), so `groundly://apd/Curs 3.pdf#page=4` is
    not a parseable uri and `\\S+` truncates it at the space; and a literal scan cannot
    invent an attribution to a file that is not in the corpus.
    """
    found: list[Attribution] = []
    claimed: list[tuple[int, int]] = []

    # The filename scan below runs against a case-folded copy so a host that retypes
    # "CURS 3.PDF" is still credited. Every span is then applied to the *original* text,
    # which is only sound while folding preserves length — it does not for a handful of
    # characters (German ß folds to "ss"), and a length change would shift every span
    # after it onto the wrong substring. Rare enough to be worth a guard rather than a
    # slower character-by-character scan, and silently mis-slicing an answer is exactly
    # the kind of wrong number this experiment cannot afford.
    folded_text = text.casefold()
    if len(folded_text) != len(text):
        folded_text = text

    for match in _CHUNK_RE.finditer(text):
        chunk_id = int(match.group(1))
        found.append(
            Attribution(
                raw=match.group(0),
                kind="chunk",
                # A cited id is only resolvable if it exists in this subject at all.
                # Whether it was *retrieved for this question* is the caller's check
                # (see `resolvable`) — the two are different failures and get counted
                # separately: an id from another subject is a hallucination, an id from
                # this subject that was not retrieved is a citation of unread material.
                resolved=frozenset({chunk_id}) if chunk_id in index.chunk_ids else frozenset(),
                span=match.span(),
            )
        )
        claimed.append(match.span())

    for filename in index.filenames:
        needle = filename.casefold() if folded_text is not text else filename
        start = folded_text.find(needle)
        while start != -1:
            end = start + len(filename)
            page, end = _page_after(folded_text, end)
            if not any(s < end and start < e for s, e in claimed):
                found.append(
                    Attribution(
                        # `raw` comes from the original text: what the answer's author
                        # actually wrote is what gets quoted in the results file.
                        raw=text[start:end],
                        kind="uri" if _URI_BEFORE_RE.search(folded_text[:start]) else "prose",
                        resolved=index.resolve(filename, page),
                        span=(start, end),
                    )
                )
                claimed.append((start, end))
            start = folded_text.find(needle, end)

    seen: set[tuple] = set()
    unique = []
    for attribution in sorted(found, key=lambda a: a.span):
        if attribution.key not in seen:
            seen.add(attribution.key)
            unique.append(attribution)
    return unique


def resolvable(attributions: list[Attribution], seen: set[int]) -> list[Attribution]:
    """The subset whose resolved chunks intersect what the answer's author actually
    retrieved.

    Intersection, not containment: a `(filename, page)` attribution resolves to every
    chunk on that page, and the author saw some of them. Demanding all of them would
    score the host down for the granularity of its citation syntax rather than for its
    accuracy — the exact artifact the three-layer split exists to prevent.
    """
    return [a for a in attributions if a.resolved & seen]


def strip(text: str, attributions: list[Attribution]) -> str:
    """`text` with every attribution removed, for handing to a blind judge.

    **Blinding is partial and the results document says so.** Removing the markers stops
    the judge classifying by `[chunk N]`, which is the achievable part; it cannot hide
    that the two paths write in different house styles. Overclaiming this would be worse
    than not doing it — the judge-agreement rate and the human spot-check are what the
    faithfulness numbers actually rest on.
    """
    out = []
    cursor = 0
    for attribution in sorted(attributions, key=lambda a: a.span):
        start, end = attribution.span
        if start < cursor:
            continue
        out.append(text[cursor:start])
        cursor = end
    out.append(text[cursor:])
    # Tidy what the removal orphans ("According to (), deadlocks" -> "According to,
    # deadlocks"), so the gap itself is not the tell the stripping was meant to remove.
    stripped = "".join(out)
    stripped = re.sub(r"\(\s*\)|\[\s*\]", "", stripped)  # emptied brackets
    stripped = re.sub(r"[ \t]{2,}", " ", stripped)  # runs left by the excision
    stripped = re.sub(r" +([,.;:)\]])", r"\1", stripped)  # space before punctuation
    return stripped.strip()
