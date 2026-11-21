"""Accuracy of extracted page text against a reference, as multiset word and
bigram F1 plus Romanian-diacritic recall. Scores are aggregates; page text is
never written anywhere."""

import itertools
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import pypdfium2 as pdfium

_DIACRITICS = set("ăâîșțşţ")
_FOLD = str.maketrans("şţ", "șț")
_IMAGE = re.compile(r"<!--\s*image\s*-->")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MARKUP = re.compile(r"[#*|`]")
_METRICS = ("word_f1", "bigram_f1", "diacritic_recall", "diacritic_recall_folded")


def _tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFC", text)
    text = _MARKUP.sub(" ", _LINK.sub(r"\1", _IMAGE.sub(" ", text))).lower()
    return [w for w in re.findall(r"\w+", text) if len(w) > 2]


def words(text: str) -> Counter[str]:
    return Counter(_tokens(text))


def _f1(ref: Counter, hyp: Counter) -> float:
    if not ref and not hyp:
        return 1.0
    hit = sum((ref & hyp).values())
    if hit == 0:
        return 0.0
    p, r = hit / sum(hyp.values()), hit / sum(ref.values())
    return 2 * p * r / (p + r)


def word_f1(ref: str, hyp: str) -> float:
    return _f1(words(ref), words(hyp))


def bigram_f1(ref: str, hyp: str) -> float:
    def bigrams(t: str) -> Counter:
        w = _tokens(t)
        return Counter(itertools.pairwise(w))

    return _f1(bigrams(ref), bigrams(hyp))


def diacritic_recall(ref: str, hyp: str, *, fold: bool) -> float | None:
    """Share of the reference's diacritic words found in the hypothesis. Folding
    treats cedilla and comma-below forms as equal."""
    if fold:
        ref, hyp = ref.translate(_FOLD), hyp.translate(_FOLD)
    want = Counter({w: n for w, n in words(ref).items() if _DIACRITICS & set(w)})
    if not want:
        return None
    return sum((want & words(hyp)).values()) / sum(want.values())


def reference_text(page: dict, reference_dir: Path) -> str | None:
    """A `ref` page reads its checked transcription (None until it exists); any
    other page reads the PDF's own text layer."""
    if page.get("ref"):
        f = reference_dir / f"{page['sha256']}-{page['page']}.txt"
        return f.read_text() if f.is_file() else None
    pdf = pdfium.PdfDocument(Path(page["path_hint"]).expanduser())
    try:
        pg = pdf[page["page"] - 1]
        try:
            tp = pg.get_textpage()
            try:
                return tp.get_text_range()
            finally:
                tp.close()
        finally:
            pg.close()
    finally:
        pdf.close()


def score(manifest: dict, outputs: list[dict], reference_dir: Path) -> dict[str, float]:
    by_page = {(o["sha256"], o["page"]): o for o in outputs}
    acc: dict[str, list[float]] = defaultdict(list)
    failed = scored = 0
    for page in manifest["pages"]:
        ref = reference_text(page, reference_dir)
        if ref is None:  # reference not checked yet: not scorable
            continue
        scored += 1
        out = by_page.get((page["sha256"], page["page"]))
        broken = out is None or out.get("error")
        failed += bool(broken)
        hyp = "" if broken else out["text"]
        suffix = f"{page['kind']}/{page['lang']}"
        acc[f"word_f1/{suffix}"].append(word_f1(ref, hyp))
        acc[f"bigram_f1/{suffix}"].append(bigram_f1(ref, hyp))
        for name, fold in (("diacritic_recall", False), ("diacritic_recall_folded", True)):
            d = diacritic_recall(ref, hyp, fold=fold)
            if d is not None:
                acc[f"{name}/{suffix}"].append(d)
    result = {k: sum(v) / len(v) for k, v in acc.items()}
    return {**result, "failed_pages": failed, "scored_pages": scored}
