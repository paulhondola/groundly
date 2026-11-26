"""Accuracy of extracted page text against a reference, as multiset word and
bigram F1, word precision and recall, plus Romanian-diacritic recall: per page
(`page_scores`, the store's `page` rows) and as per-cell means (`score`).

`rescore` recomputes stored page scores from `page_text` when the scorer changes
(`SCORER_VERSION`) or a reference is corrected (`--all`)."""

import argparse
import itertools
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import pypdfium2 as pdfium

SCORER_VERSION = 1
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


def _broken(out: dict | None) -> bool:
    return out is None or bool(out.get("error"))


def page_scores(manifest: dict, outputs: list[dict], reference_dir: Path) -> list[dict]:
    """One row per page that has a reference; a broken page scores 0 even on a blank one."""
    by_page = {(o["sha256"], o["page"]): o for o in outputs}
    rows = []
    for page in manifest["pages"]:
        out = by_page.get((page["sha256"], page["page"]))
        ref = reference_text(page, reference_dir)
        if ref is None:  # reference not checked yet: not scorable
            continue
        broken = _broken(out)
        hyp = "" if broken or out is None else out["text"]
        rows.append(
            {"sha256": page["sha256"], "page": page["page"], "kind": page.get("kind"),
             "lang": page.get("lang"), "broken": broken,
             "f1": 0.0 if broken else word_f1(ref, hyp),
             "precision": 0.0 if broken else word_precision(ref, hyp),
             "recall": 0.0 if broken else word_recall(ref, hyp),
             "bigram_f1": 0.0 if broken else bigram_f1(ref, hyp),
             "diacritic_recall": diacritic_recall(ref, hyp, fold=False),
             "diacritic_recall_folded": diacritic_recall(ref, hyp, fold=True)}
        )  # fmt: skip
    return rows


_CELL_METRICS = (
    ("word_f1", "f1"), ("bigram_f1", "bigram_f1"), ("word_precision", "precision"),
    ("word_recall", "recall"), ("diacritic_recall", "diacritic_recall"),
    ("diacritic_recall_folded", "diacritic_recall_folded"),
)  # fmt: skip


def score(manifest: dict, outputs: list[dict], reference_dir: Path) -> dict[str, float]:
    by_page = {(o["sha256"], o["page"]): o for o in outputs}
    failed = sum(_broken(by_page.get((p["sha256"], p["page"]))) for p in manifest["pages"])
    rows = page_scores(manifest, outputs, reference_dir)
    acc: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        suffix = f"{r['kind']}/{r['lang']}"
        for metric, key in _CELL_METRICS:
            if r[key] is not None:  # diacritic recall only where the reference has diacritics
                acc[f"{metric}/{suffix}"].append(r[key])
    result = {k: sum(v) / len(v) for k, v in acc.items()}
    # n behind the averages: differs per metric (diacritic pages only)
    for k, v in acc.items():
        metric, suffix = k.split("/", 1)
        if metric == "word_f1":
            result[f"pages/{suffix}"] = len(v)
        elif metric == "diacritic_recall":
            result[f"diacritic_pages/{suffix}"] = len(v)
    skipped = len(manifest["pages"]) - len(rows)
    return {**result, "failed_pages": failed, "scored_pages": len(rows), "skipped_pages": skipped}


_SCORE_COLUMNS = ("f1", "precision", "recall", "bigram_f1", "diacritic_recall")


def rescore(con, reference_dir: Path, manifest: dict, *, every: bool = False) -> int:
    """Recompute stored page scores from `page_text`; stale rows only unless `every`.

    A page the manifest no longer has, or with no reference, gets NULL scores."""
    info = {(p["sha256"], p["page"]): p for p in manifest["pages"]}
    stale = "" if every else "where p.scorer_version is distinct from ?"
    rows = con.execute(
        "select p.run, p.sha256, p.page, t.text, p.error from page p "
        f"join page_text t using (run, sha256, page) {stale}",
        [] if every else [SCORER_VERSION],
    ).fetchall()
    for run, sha, page, text, error in rows:
        mp = info.get((sha, page))
        got = page_scores({"pages": [mp]}, [{"sha256": sha, "page": page, "text": text or "", "error": error}],
                          reference_dir) if mp else []  # fmt: skip
        values = [got[0][c] for c in _SCORE_COLUMNS] if got else [None] * len(_SCORE_COLUMNS)
        con.execute(
            f"update page set {', '.join(f'{c} = ?' for c in _SCORE_COLUMNS)}, scorer_version = ? "
            "where run = ? and sha256 = ? and page = ?",
            [*values, SCORER_VERSION, run, sha, page],
        )
    return len(rows)


def main() -> None:
    from bench.common import store
    from bench.common.corpus import load_manifest

    root = Path(__file__).parents[2]
    ap = argparse.ArgumentParser(prog="python -m bench.extract.score")
    ap.add_argument("command", choices=["rescore"])
    ap.add_argument("--manifest", type=Path, default=root / "bench/corpus/manifest.json")
    ap.add_argument("--reference-dir", type=Path, default=root / "bench/reference")
    ap.add_argument(
        "--all", action="store_true", help="every page row, e.g. after correcting references"
    )
    args = ap.parse_args()
    con = store.connect()
    try:
        print(
            f"rescored {rescore(con, args.reference_dir, load_manifest(args.manifest), every=args.all)} page rows"
        )
    finally:
        con.close()


if __name__ == "__main__":
    main()
