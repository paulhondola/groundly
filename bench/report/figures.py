"""One PNG per result, drawn from its Parquet export (matplotlib, `bench` group).

Colour follows the entity: every arm keeps its slot in every figure, in the validated
categorical order (light mode, for print). Bars show the median over runs with the range as
whiskers (R1 shows its bootstrap 95% CI instead); one axis per panel, never two scales.
"""

import statistics
from collections import defaultdict
from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter

SLOTS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948")
ENTITIES = ("docling", "product", "mineru", "granite", "docling_mineru", "docling_granite",
            "mlx", "torch")  # fmt: skip
SURFACE, INK, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": MUTED, "axes.labelcolor": MUTED, "text.color": INK,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.grid": True, "axes.grid.axis": "y",
    "grid.color": GRID, "grid.linewidth": 0.6, "axes.spines.top": False,
    "axes.spines.right": False, "font.size": 9, "lines.linewidth": 1.5, "legend.frameon": False,
})  # fmt: skip


def color(entity: str, seen: dict[str, str] = {}) -> str:  # noqa: B006 - one map per process
    """An entity's slot: known arms fixed, others the next free slot by first appearance."""
    if entity not in seen:
        known = ENTITIES.index(entity) if entity in ENTITIES else None
        free = [s for s in SLOTS[len(ENTITIES) :]] or SLOTS
        seen[entity] = SLOTS[known] if known is not None else free[len(seen) % len(free)]
    return seen[entity]


def _empty(ax) -> None:
    ax.text(0.5, 0.5, "no data", ha="center", va="center", color=MUTED, transform=ax.transAxes)
    ax.set(xticks=[], yticks=[])


def _legend(ax) -> None:
    """Beside the plot, never over the marks."""
    ax.legend(loc="upper left", bbox_to_anchor=(1.0, 1.0), fontsize=8)


def _rows(path: Path) -> list[dict]:
    cur = duckdb.connect().execute("select * from read_parquet(?)", [str(path)])
    cols = [d[0] for d in cur.description or []]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _vals(rows, y):
    return [r[y] for r in rows if r.get(y) is not None]


def bars(ax, rows, x, y, series=None, lo=None, hi=None):
    """Median of `y` per (x, series), whiskers = range over runs, or [lo, hi] when given."""
    series = series or x
    if not _vals(rows, y):
        _empty(ax)
        return
    cats = list(dict.fromkeys(r[x] for r in rows))
    groups = list(dict.fromkeys(r[series] for r in rows if r.get(y) is not None))
    cell = defaultdict(list)
    for r in rows:
        cell[r[x], r[series]].append(r)
    own = series == x  # one bar per category, coloured as its own entity
    width = 0.6 if own else 0.8 / len(groups)
    for g_i, g in enumerate(groups):
        xs, hs, err = [], [], [[], []]
        for c_i, c in enumerate(cats):
            v = _vals(cell[c, g], y)
            if not v:
                continue
            h = statistics.median(v)
            low = min(_vals(cell[c, g], lo) or [h]) if lo else min(v)
            high = max(_vals(cell[c, g], hi) or [h]) if hi else max(v)
            xs.append(c_i if own else c_i + (g_i - (len(groups) - 1) / 2) * width)
            hs.append(h)
            err[0].append(h - low)
            err[1].append(high - h)
        ax.bar(xs, hs, width * 0.92, color=color(str(g)) if own else SLOTS[g_i], label=str(g), yerr=err,
               error_kw={"ecolor": MUTED, "elinewidth": 1, "capsize": 2})  # fmt: skip
    ax.set_xticks(range(len(cats)), [str(c) for c in cats], rotation=20, ha="right")
    ax.set_ylabel(y)
    if series != x:
        _legend(ax)


def stacked(ax, rows, x, ys):
    cats = list(dict.fromkeys(r[x] for r in rows))
    bottom = [0.0] * len(cats)
    for i, y in enumerate(ys):
        h = [statistics.median(_vals([r for r in rows if r[x] == c], y) or [0]) for c in cats]
        ax.bar(range(len(cats)), h, 0.6, bottom=bottom, color=SLOTS[i], label=y,
               edgecolor=SURFACE, linewidth=2)  # fmt: skip
        bottom = [b + v for b, v in zip(bottom, h, strict=True)]
    ax.set_xticks(range(len(cats)), [str(c) for c in cats], rotation=20, ha="right")
    ax.set_ylabel("seconds (median)")
    _legend(ax)


def box(ax, rows, y, x=None):
    cats = list(dict.fromkeys(r[x] for r in rows)) if x else [y]
    data = [_vals([r for r in rows if not x or r[x] == c], y) for c in cats]
    keep = [(c, d) for c, d in zip(cats, data, strict=True) if d]
    if not keep:
        _empty(ax)
        return
    parts = ax.boxplot(
        [d for _, d in keep],
        patch_artist=True,
        widths=0.5,
        medianprops={"color": INK, "linewidth": 1.5},
    )
    for patch, (c, _) in zip(parts["boxes"], keep, strict=True):
        patch.set(facecolor=color(str(c)) if x else SLOTS[0], alpha=0.85, edgecolor=MUTED)
    ax.set_xticks(range(1, len(keep) + 1), [str(c) for c, _ in keep], rotation=20, ha="right")
    ax.set_ylabel(y)


def cdf(ax, rows, y, group):
    for g in dict.fromkeys(r[group] for r in rows):
        v = sorted(_vals([r for r in rows if r[group] == g], y))
        ax.step(v, [(i + 1) / len(v) for i in range(len(v))], where="post", color=color(str(g)),
                label=str(g))  # fmt: skip
    ax.set(xlabel=y, ylabel="share of pages", xscale="log")
    ax.xaxis.set_major_locator(LogLocator(subs=(1, 2, 5)))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    ax.xaxis.set_minor_formatter(NullFormatter())
    _legend(ax)


def lines(ax, rows, x, y, group):
    for g in dict.fromkeys(r[group] for r in rows):
        pts = [(r[x], r[y]) for r in rows if r[group] == g and r[y] is not None]
        ax.plot([p[0] for p in pts], [p[1] for p in pts], color=color(str(g)), label=str(g))
    ax.set(xlabel=x, ylabel=y)
    _legend(ax)


def scatter(ax, rows, x, y, group):
    for r in rows:
        if r[x] is None or r[y] is None:
            continue
        ax.scatter(r[x], r[y], s=40, color=color(str(r[group])), edgecolor=SURFACE, linewidth=2)
        ax.annotate(str(r[group]), (r[x], r[y]), textcoords="offset points", xytext=(5, 4),
                    color=MUTED, fontsize=8)  # fmt: skip
    ax.set(xlabel=x, ylabel=y)


def _first_run(rows):
    return [r for r in rows if r.get("run_no") == 1]


# result -> (figure title, [(panel title, draw, keyword args, row filter)])
PANELS = {
    "R1": ("Word F1 per engine and page cell (bootstrap 95% CI)",
           [("", bars, {"x": "cell", "y": "f1", "series": "arm", "lo": "f1_lo", "hi": "f1_hi"}, None)]),
    "R2": ("Per-page word F1", [("", box, {"x": "arm", "y": "f1"}, None)]),
    "R3": ("Cost vs accuracy", [("time", scatter, {"x": "s_per_page", "y": "f1", "group": "arm"}, None),
                                ("energy", scatter, {"x": "j_per_page", "y": "f1", "group": "arm"}, None)]),
    "R4": ("Per-page latency", [("", cdf, {"y": "ms", "group": "arm"}, None)]),
    "R5": ("Memory over time (physical footprint, run 1)",
           [(b, lines, {"x": "t_s", "y": "footprint_mb", "group": "arm"},
             lambda rs, b=b: [r for r in _first_run(rs) if r["bench"] == b]) for b in ("extract", "index")]),
    "R6": ("Energy and CPU per item", [("energy", bars, {"x": "arm", "y": "j_per_item", "series": "bench"}, None),
                                       ("CPU", bars, {"x": "arm", "y": "cpu_pct", "series": "bench"}, None)]),
    "R7": ("Failures", [(y, bars, {"x": "arm", "y": y, "series": "bench"}, None)
                        for y in ("capped_pages", "failed_items", "timed_out")]),
    "R8": ("Index time", [("wall", bars, {"x": "arm", "y": "wall_s", "series": "subject"}, None),
                          ("stage split", stacked, {"x": "arm", "ys": ["extract_s", "embed_s", "other_s"]}, None)]),
    "R9": ("Retrieval hit@8 (within one page)", [("", bars, {"x": "arm", "y": "hit_within1_at8", "series": "subject"}, None)]),
    "R10": ("Embedding and rerank throughput", [(y, bars, {"x": "arm", "y": y}, None)
                                                for y in ("chunks_per_s", "pairs_per_s", "load_s")]),
    "R11": ("search latency", [(y, bars, {"x": "arm", "y": y}, None) for y in ("p50_ms", "p95_ms")]),
    "R12": ("Embed resources", [(y, bars, {"x": "arm", "y": y}, None)
                                for y in ("j_per_1000_chunks", "peak_footprint_mb", "cpu_pct", "gpu_active")]),
    "R13": ("MLX vs torch agreement", [(y, box, {"y": y}, None)
                                       for y in ("dense_cos", "sparse_top10_overlap", "rerank_top5_overlap", "max_score_diff")]),
    "R14": ("hit@8 per engine", [("", bars, {"x": "arm", "y": "hit_within1_at8", "series": "subject"}, None)]),
    "R15": ("memray peak heap", [("", bars, {"x": "arm", "y": "peak_heap_mb", "series": "process"},
                                  lambda rs: [r for r in rs if r["kind"] == "memray"])]),
}  # fmt: skip


def draw(name: str, parquet: Path, png: Path) -> Path:
    title, panels = PANELS[name]
    rows = _rows(parquet)
    fig, axes = plt.subplots(
        1, len(panels), figsize=(max(7.5, 4.6 * len(panels)), 3.8), squeeze=False
    )
    for ax, (sub, fn, kw, keep) in zip(axes[0], panels, strict=True):
        part = keep(rows) if keep else rows
        if part:
            fn(ax, part, **kw)
        else:
            _empty(ax)
        ax.set_title(sub, color=MUTED, fontsize=9, loc="left")
    fig.suptitle(f"{name} · {title}", x=0.01, ha="left", fontsize=11)
    fig.tight_layout()
    fig.savefig(png, dpi=200)
    plt.close(fig)
    return png
