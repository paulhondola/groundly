"""Accuracy of extracted page text against a reference, as multiset word and
bigram F1, word precision and recall, plus Romanian-diacritic recall. Scores are aggregates; page text is
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
_METRICS = (
    "word_f1",
    "bigram_f1",
    "word_precision",
    "word_recall",
    "diacritic_recall",
    "diacritic_recall_folded",
)


def _tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFC", text)
    text = _MARKUP.sub(" ", _LINK.sub(r"\1", _IMAGE.sub(" ", text))).lower()
    return [w for w in re.findall(r"\w+", text) if len(w) > 2]


def words(text: str) -> Counter[str]:
    return Counter(_tokens(text))


def _pr(ref: Counter, hyp: Counter) -> tuple[float, float]:
    """Multiset precision and recall; an empty side scores 1 only against an empty other."""
    hit = sum((ref & hyp).values())
    p = hit / sum(hyp.values()) if hyp else float(not ref)
    r = hit / sum(ref.values()) if ref else float(not hyp)
    return p, r


def _f1(ref: Counter, hyp: Counter) -> float:
    p, r = _pr(ref, hyp)
    return 2 * p * r / (p + r) if p + r else 0.0


def word_f1(ref: str, hyp: str) -> float:
    return _f1(words(ref), words(hyp))


def word_precision(ref: str, hyp: str) -> float:
    return _pr(words(ref), words(hyp))[0]


def word_recall(ref: str, hyp: str) -> float:
    return _pr(words(ref), words(hyp))[1]


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


def _text_layer(path_hint: str, page_no: int) -> str:
    pdf = pdfium.PdfDocument(Path(path_hint).expanduser())
    try:
        pg = pdf[page_no - 1]
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


def reference_text(page: dict, reference_dir: Path) -> str | None:
    """A `ref` page reads its checked transcription (None until it exists). A rendered
    page reads its source page's text layer. An image page with no transcription has no
    reference; any other page reads the PDF's own text layer."""
    if page.get("ref"):
        f = reference_dir / f"{page['sha256']}-{page['page']}.txt"
        return f.read_text(encoding="utf-8") if f.is_file() else None
    if page.get("source") == "rendered":
        src = page["render_of"]
        return _text_layer(src["path_hint"], src["page"])
    if page.get("kind") in ("image", "standalone"):
        return None
    return _text_layer(page["path_hint"], page["page"])


def score(manifest: dict, outputs: list[dict], reference_dir: Path) -> dict[str, float]:
    by_page = {(o["sha256"], o["page"]): o for o in outputs}
    acc: dict[str, list[float]] = defaultdict(list)
    failed = scored = skipped = 0
    for page in manifest["pages"]:
        out = by_page.get((page["sha256"], page["page"]))
        broken = out is None or bool(out.get("error"))
        failed += broken
        ref = reference_text(page, reference_dir)
        if ref is None:  # reference not checked yet: not scorable
            skipped += 1
            continue
        scored += 1
        hyp = "" if broken else out["text"]
        suffix = f"{page['kind']}/{page['lang']}"
        # a broken page scores 0 even against a blank reference
        acc[f"word_f1/{suffix}"].append(0.0 if broken else word_f1(ref, hyp))
        acc[f"bigram_f1/{suffix}"].append(0.0 if broken else bigram_f1(ref, hyp))
        acc[f"word_precision/{suffix}"].append(0.0 if broken else word_precision(ref, hyp))
        acc[f"word_recall/{suffix}"].append(0.0 if broken else word_recall(ref, hyp))
        for name, fold in (("diacritic_recall", False), ("diacritic_recall_folded", True)):
            d = diacritic_recall(ref, hyp, fold=fold)
            if d is not None:
                acc[f"{name}/{suffix}"].append(d)
    result = {k: sum(v) / len(v) for k, v in acc.items()}
    # n behind the averages: differs per metric (diacritic pages only)
    for k, v in acc.items():
        metric, suffix = k.split("/", 1)
        if metric == "word_f1":
            result[f"pages/{suffix}"] = len(v)
        elif metric == "diacritic_recall":
            result[f"diacritic_pages/{suffix}"] = len(v)
    return {**result, "failed_pages": failed, "scored_pages": scored, "skipped_pages": skipped}
