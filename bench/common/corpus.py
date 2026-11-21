"""Corpus census, page language tag and the pinned sample manifest.

A manifest row carries hashes and page-level flags only, never page text; the
file hash pins the bytes so a changed PDF fails loudly instead of skewing a run.
"""

import hashlib
import json
import random
import re
from collections import Counter
from datetime import UTC, datetime
from itertools import zip_longest
from pathlib import Path
from typing import Literal

import pypdfium2 as pdfium

from groundly.ingestion.pdf_router import classify_page, is_slide_deck

_RO = {
    "și",
    "şi",
    "în",
    "de",
    "la",
    "cu",
    "pentru",
    "este",
    "care",
    "sau",
    "din",
    "pe",
    "un",
    "o",
    "ale",
    "sunt",
    "mai",
}
_EN = {"the", "of", "and", "to", "is", "in", "for", "that", "with", "are", "be", "this", "on", "as"}
_RO_DIACRITICS = set("ăâîșțşţ")

_HOME = str(Path.home())


def page_lang(text: str) -> Literal["ro", "en", "?"]:
    lowered = text.lower()
    words = re.findall(r"\w+", lowered)
    ro = sum(w in _RO for w in words) + 3 * sum(c in _RO_DIACRITICS for c in lowered)
    en = sum(w in _EN for w in words)
    if ro == en == 0:
        return "?"
    return "ro" if ro > en else "en"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hint(path: Path) -> str:
    s = str(path)
    return "~" + s[len(_HOME) :] if s.startswith(_HOME + "/") else s


def census(pdfs: list[tuple[str, Path]]) -> list[dict]:
    """One row per page. An unknown-language page takes its document's majority."""
    rows: list[dict] = []
    for subject, path in pdfs:
        sha, hint = _sha256(path), _hint(path)
        doc_rows: list[dict] = []
        pdf = pdfium.PdfDocument(path)
        try:
            deck = is_slide_deck(pdf)
            for n, page in enumerate(pdf, start=1):
                tp = page.get_textpage()
                try:
                    text = tp.get_text_range()
                finally:
                    tp.close()
                doc_rows.append(
                    {
                        "subject": subject,
                        "sha256": sha,
                        "path_hint": hint,
                        "page": n,
                        "kind": classify_page(page),
                        "lang": page_lang(text),
                        "deck": deck,
                    }
                )
        finally:
            pdf.close()
        known = Counter(r["lang"] for r in doc_rows if r["lang"] != "?")
        if known:
            majority = known.most_common(1)[0][0]
            for r in doc_rows:
                if r["lang"] == "?":
                    r["lang"] = majority
        rows += doc_rows
    return rows


def sample(rows: list[dict], n_total: int = 200, n_ref: int = 40, seed: int = 0) -> list[dict]:
    """Reference pages (OCR kinds, half RO / half EN where possible) plus the
    rest spread evenly over the (kind, lang, deck) strata present."""
    rng = random.Random(seed)
    ordered = sorted(rows, key=lambda r: (r["sha256"], r["page"]))
    pool = [r for r in ordered if r["kind"] in ("mixed", "image")]
    by_lang = {lang: [r for r in pool if r["lang"] == lang] for lang in ("ro", "en")}
    for v in by_lang.values():
        rng.shuffle(v)
    half = n_ref // 2
    take = {"ro": min(half, len(by_lang["ro"])), "en": min(n_ref - half, len(by_lang["en"]))}
    for a, b in (("ro", "en"), ("en", "ro")):  # top up from the other language
        take[a] = min(len(by_lang[a]), take[a] + max(0, n_ref - sum(take.values())))
    refs = by_lang["ro"][: take["ro"]] + by_lang["en"][: take["en"]]
    chosen = {(r["sha256"], r["page"]) for r in refs}

    strata: dict[tuple, list[dict]] = {}
    for r in ordered:
        if (r["sha256"], r["page"]) not in chosen:
            strata.setdefault((r["kind"], r["lang"], r["deck"]), []).append(r)
    queues = [strata[k] for k in sorted(strata)]
    for q in queues:
        rng.shuffle(q)
    rest = [r for batch in zip_longest(*queues) for r in batch if r is not None]
    rest = rest[: n_total - len(refs)]  # round-robin draw keeps strata even
    return [{**r, "ref": True} for r in refs] + [{**r, "ref": False} for r in rest]


def write_manifest(pages: list[dict], path: Path, version: int = 1) -> None:
    doc = {"version": version, "created": datetime.now(UTC).isoformat(), "pages": pages}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1))


def load_manifest(path: Path) -> dict:
    """Re-hash every distinct file; name the path_hint on a missing file or mismatch."""
    doc = json.loads(path.read_text())
    seen: dict[str, str] = {}
    for p in doc["pages"]:
        seen.setdefault(p["path_hint"], p["sha256"])
    for hint, sha in seen.items():
        f = Path(hint).expanduser()
        if not f.is_file():
            raise FileNotFoundError(f"manifest file missing: {hint}")
        if _sha256(f) != sha:
            raise ValueError(f"manifest sha256 mismatch: {hint}")
    return doc
