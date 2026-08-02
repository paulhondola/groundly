"""Self-contained HTML visualization of a subject's graphrag knowledge graph
(`groundly export-graph`) — entity nodes coloured by Leiden community, force-directed,
with a sidebar of community reports and citations back to source documents.

Self-contained by requirement (.claude/rules/grounding-and-privacy.md: no egress beyond
the student's own provider, HF downloads, and pinned OCR models — a CDN script is none
of those). vis-network and theme.css are inlined; the page works offline forever.

Entity titles/descriptions come from course PDFs, which a hostile `groundly import`
bundle can populate with anything (layer 4, trusted content never trusted authority —
.claude/rules/grounding-and-privacy.md). So the two hard rules here: (1) every piece of
graph data is embedded as one escaped `json.dumps` blob, never string-concatenated into
markup; (2) the JS reaches the DOM only via textContent/createElement, never innerHTML.
"""

import json
import math
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq  # row-count metadata + bounded batch reads — see _read_bounded

from groundly.core.store import SubjectStore
from groundly.core.subject import Subject

# Real subjects measured so far: test_graph 114 entities, gm-validate 385. Extraction
# density varies a lot with material — gm-validate (PDF slides) yields 4.3 entities per
# chunk, test_graph (markdown prose) 8.1 — so a 1,194-chunk subject lands somewhere
# between ~5,200 and ~9,700 entities, i.e. over this cap either way. vis-network's
# forceAtlas2Based physics stops being interactively draggable well before that. Above
# the cap we render one node per community (the "aggregated" view) rather than serve an
# unusable page.
_MAX_NODES = 5000

# Per-field display cap — see _text. _MAX_NODES bounds *how many* things are drawn;
# this bounds how big each one may be, which is the other half of the resource question
# once field contents are attacker-controlled.
_MAX_FIELD_CHARS = 8000

# Uncompressed ceiling per parquet artifact, read from the footer before any decompression
# — see _refuse_oversized_artifacts. Generous: a real subject's whole graph/ is a few MB.
_MAX_ARTIFACT_BYTES = 512 * 1024 * 1024

# Rows per streaming batch in _read_bounded. Peak memory is the limit plus one batch, and
# a single row's size is itself attacker-controlled, so no row count bounds bytes outright
# — a smaller batch just tightens how far past the limit the overshoot can go before the
# check fires. 512 keeps that overshoot small without making a real subject's read chatty.
_READ_BATCH_ROWS = 512

# The only columns the aggregated (community meta-graph) view needs. Reading just these
# keeps an inflated `description` column off the heap entirely on the one path where the
# entity count already says the file is huge.
_AGGREGATED_ENTITY_COLUMNS = ["id", "title"]

# Every artifact this module reads. Checked together up front: a build interrupted
# between graphrag's workflows leaves some of them and not others.
_REQUIRED_ARTIFACTS = (
    "entities.parquet",
    "relationships.parquet",
    "communities.parquet",
    "community_reports.parquet",
    "text_units.parquet",
)

# Tableau10 — the same categorical palette graphify's graph.html uses, so this page and
# a future P7 dashboard read as one product.
_PALETTE = [
    "#4E79A7",
    "#F28E2B",
    "#E15759",
    "#76B7B2",
    "#59A14F",
    "#EDC948",
    "#B07AA1",
    "#FF9DA7",
    "#9C755F",
    "#BAB0AC",
]
_NO_COMMUNITY_COLOR = "#5b5b66"


class GraphHtmlError(RuntimeError):
    """A subject that cannot be visualized — named cause, never a traceback."""


@dataclass(frozen=True)
class GraphHtmlResult:
    path: Path
    nodes: int
    edges: int
    communities: int
    aggregated: bool  # True when the community meta-graph was rendered instead of entities


def _citation_line(filename: str, page: int | None, heading_path: str | None) -> str:
    """Mirrors anki.py::_source_line's conditional style, but markdown chunks have
    page=None (no PDF page to cite), so a bare `page is not None` check — not falling
    through to heading_path when page is merely absent-but-zero-ish — is what keeps
    the `documents.title`-style "knowledge-base.md#pNone" bug off this sidebar."""
    if page is not None:
        return f"{filename}, p.{page}"
    if heading_path:
        return f"{filename} \u203a {heading_path}"
    return filename


def _num(value, default=0):
    """A parquet cell as a JSON-safe python number. graphrag leaves numeric columns as
    numpy scalars (json.dumps doesn't know them) and NaN for optional community-report
    fields like rank/rating_explanation (json.dumps emits the bare token `NaN`, which
    is not valid JSON and fails a strict `JSON.parse` in the browser)."""
    if value is None:
        return default
    if isinstance(value, float) and math.isnan(value):
        return default
    if isinstance(value, np.floating):
        return default if np.isnan(value) else float(value)
    if isinstance(value, np.integer):
        return int(value)
    return value


def _text(value, default=""):
    """A parquet cell as display text, length-capped.

    The cap is a resource bound, not formatting. Every string on the page originates in
    course material, and an imported bundle is untrusted (grounding-and-privacy.md), so
    field length is attacker-controlled: measured, 100 entities carrying 1 MiB
    descriptions — 50x *under* the _MAX_NODES cap, so no other guard fires — produced a
    300 MiB HTML file. _MAX_NODES bounds how many things are drawn; this bounds how big
    each one can be. 8k chars is far past any real description (longest measured on a real
    subject: 706) and still leaves the worst case bounded at tens of MB.

    This is the one choke point every attacker-controlled string passes through on its way
    into the payload — keep it that way rather than capping at each call site."""
    if value is None:
        return default
    if isinstance(value, float) and math.isnan(value):
        return default
    text = str(value)
    return text if len(text) <= _MAX_FIELD_CHARS else text[:_MAX_FIELD_CHARS] + "…"


def _iter(value) -> list:
    """entity_ids/text_unit_ids/etc. come back from parquet as a python list or a
    numpy object array depending on the column's storage — never assume one form."""
    if isinstance(value, list | np.ndarray):
        return list(value)
    return []


def _findings(value) -> list[dict]:
    """community_reports.findings is a numpy ndarray of dicts (or NaN when a report
    has none) — json.dumps needs a plain list of plain dicts."""
    if not isinstance(value, np.ndarray):
        return []
    return [
        {"explanation": _text(f.get("explanation")), "summary": _text(f.get("summary"))}
        for f in value
        if isinstance(f, dict)
    ]


def _read_bounded(path: Path, subject_name: str, columns: list[str] | None = None) -> pd.DataFrame:
    """`pd.read_parquet` with a ceiling on what it will materialize.

    `groundly import` is a trust boundary, so a bundle's *decompressed* size is the
    sender's choice, and parquet inflates repetitive text spectacularly: measured, a 63 KiB
    entities.parquet expanding to 600 MiB of pandas objects. Neither `_MAX_NODES` nor
    `_MAX_FIELD_CHARS` helps — they bound the page, and the read that feeds it has already
    happened.

    The footer is no help either, which is the trap worth recording: `total_byte_size`
    claims to be the uncompressed size, but it is the *encoded* size, and 600 identical
    1 MiB strings dictionary-encode to one value plus 600 indices. On the file above it
    reports 1.02 MiB against 600 MiB actual — a check that passes an attack it is supposed
    to catch is worse than no check, because it reads as protection.

    So bound it by actually streaming: read row-group batches, add up their real nbytes,
    and stop the moment the running total crosses the limit. Peak memory is the limit plus
    one batch, whatever the encoding. The limit is generous against any genuine course —
    gm-validate's entire graph/ is a few MB — and exists to turn an OOM into a sentence."""
    parquet = pq.ParquetFile(path)
    batches, total = [], 0
    for batch in parquet.iter_batches(batch_size=_READ_BATCH_ROWS, columns=columns):
        total += batch.nbytes
        if total > _MAX_ARTIFACT_BYTES:
            raise GraphHtmlError(
                f"{path.name} in the graph for {subject_name!r} expands past the "
                f"{_MAX_ARTIFACT_BYTES // 1_048_576} MB this export will hold in memory. "
                "A graph built from your own materials never approaches that; an imported "
                "subject that does is malformed or hostile, and was not rendered"
            )
        batches.append(batch)
    if not batches:  # a zero-row artifact still needs the right columns for downstream code
        return parquet.read(columns=columns).to_pandas()
    return pa.Table.from_batches(batches).to_pandas()


def _resolve_level(level: int | None, communities: pd.DataFrame) -> tuple[int | None, list[int]]:
    """`level=None` means the *coarsest* level (0), which is the one that actually
    colours the graph.

    Not the finest, which is the intuitive choice and the wrong one: Leiden only
    subdivides communities large enough to split, so every level below the root covers
    strictly fewer entities, and entities in no community at the chosen level render
    grey. Measured 2026-08-02 — test_graph colours 81/114 (71%) at level 0 against
    41/114 (36%) at level 1, and gm-validate 188/385 (49%) at level 0 against 12/385
    (**3%**) at level 2. Defaulting to the finest level opens gm-validate as an almost
    entirely grey graph. The page's own level toggle still reaches every level.

    An explicit level that doesn't exist is a caller error, not a silent fallback — it
    would otherwise render an empty legend with no explanation."""
    levels = (
        sorted({int(v) for v in communities["level"].unique()}) if not communities.empty else []
    )
    if level is not None and level not in levels:
        raise GraphHtmlError(
            f"level {level} has no communities — this graph has level(s) {levels or '(none)'}"
        )
    if level is not None:
        return level, levels
    return (levels[0] if levels else None), levels


def _community_colors(community_ids: list[int]) -> dict[int, str]:
    """Assigns palette colours in the given order — callers pass ids already sorted
    largest-community-first, so a graph with more communities than palette colours
    still gives the biggest, most-visible ones distinct colours before any repeat."""
    return {cid: _PALETTE[i % len(_PALETTE)] for i, cid in enumerate(community_ids)}


def _legend_for_level(
    level: int, communities: pd.DataFrame, reports_by_community: dict[int, pd.Series]
) -> list[dict]:
    """One entry per community at `level`: label, colour, and the report fields the
    sidebar shows when a legend entry or (aggregated) node is clicked."""
    rows = communities[communities["level"] == level].sort_values("size", ascending=False)
    ids = [int(c) for c in rows["community"]]
    colors = _community_colors(ids)
    legend = []
    for _, row in rows.iterrows():
        cid = int(row["community"])
        report = reports_by_community.get(cid)
        legend.append(
            {
                "community": cid,
                # communities.title is the literal string "Community N" — never a real
                # label (measured fact). community_reports.title is the LLM-generated one.
                "title": _text(report["title"]) if report is not None else f"Community {cid}",
                "size": _num(row.get("size"), 0),
                "color": colors[cid],
                "summary": _text(report["summary"]) if report is not None else "",
                "rank": _num(report.get("rank"), None) if report is not None else None,
                "rating_explanation": (
                    _text(report.get("rating_explanation")) if report is not None else ""
                ),
                "findings": _findings(report.get("findings")) if report is not None else [],
            }
        )
    return legend


def _entity_citations(
    entities: pd.DataFrame, text_units: pd.DataFrame, store: SubjectStore
) -> dict[str, list[str]]:
    """entities.text_unit_ids (text-unit hash ids) -> text_units.id -> document_id,
    which build_graph sets to str(chunk_id) directly — so it resolves straight into
    store.db's chunks table via the store's own chunk_details helper, one batch call
    for every chunk any rendered entity cites."""
    # A LIST of document_ids per text-unit id, not set_index()["document_id"]: text_unit
    # ids are content hashes, so two Groundly chunks with byte-identical text collide onto
    # one id — and they are still *different chunks*, on different pages of different
    # files. Measured on gm-validate: 3 ids collide, every one of them spanning more than
    # one document_id. With a pandas index lookup those rows make `.get()` return a Series
    # instead of a scalar, `int()` raises TypeError, and the except below swallows it — 10
    # entities (VECTOR, SIMD, SPMD, NVIDIA GPU ARCHITECTURE, ...) silently lost *every*
    # citation and 6 more lost some, while the page still claimed to resolve them.
    doc_ids_by_tu: dict[str, list] = {}
    for tu_id, doc_id in zip(text_units["id"], text_units["document_id"], strict=True):
        doc_ids_by_tu.setdefault(tu_id, []).append(doc_id)

    chunk_ids_by_entity: dict[str, list[int]] = {}
    all_chunk_ids: set[int] = set()
    for _, row in entities.iterrows():
        seen: set[int] = set()
        chunk_ids: list[int] = []
        for tu_id in _iter(row["text_unit_ids"]):
            for doc_id in doc_ids_by_tu.get(tu_id, ()):
                if doc_id is None:
                    continue
                try:
                    cid = int(doc_id)
                except (TypeError, ValueError):
                    continue
                if cid not in seen:
                    seen.add(cid)
                    chunk_ids.append(cid)
        chunk_ids_by_entity[row["id"]] = chunk_ids
        all_chunk_ids.update(chunk_ids)

    details = {r["chunk_id"]: r for r in store.chunk_details(sorted(all_chunk_ids))}

    citations_by_entity: dict[str, list[str]] = {}
    for entity_id, chunk_ids in chunk_ids_by_entity.items():
        lines = []
        for cid in chunk_ids:
            row = details.get(cid)
            if row is None:  # citation vanished between build and this export — skip
                continue
            lines.append(_citation_line(row["filename"], row["page"], row["heading_path"]))
        citations_by_entity[entity_id] = lines
    return citations_by_entity


def _entity_graph(
    entities: pd.DataFrame,
    relationships: pd.DataFrame,
    title_to_id: dict[str, str],
    entity_to_community: dict[int, dict[str, int]],
    levels: list[int],
    citations_by_entity: dict[str, list[str]],
) -> tuple[list[dict], list[dict]]:
    nodes = [
        {
            "id": row["id"],
            "label": _text(row["title"]),
            "type": _text(row.get("type")),
            "description": _text(row.get("description")),
            "degree": _num(row.get("degree"), 0),
            # every level at once, so the level toggle recolours client-side with no
            # server round trip
            "communities": {str(lvl): entity_to_community[lvl].get(row["id"]) for lvl in levels},
            "citations": citations_by_entity.get(row["id"], []),
        }
        for _, row in entities.iterrows()
    ]
    edges = []
    for _, row in relationships.iterrows():
        src = title_to_id.get(row["source"])
        tgt = title_to_id.get(row["target"])
        if src is None or tgt is None:  # relationship refers to a title we never saw
            continue
        # No `description`: the page renders node and community-report text but has no
        # surface that ever shows a relationship's description, so shipping it put layer-4
        # course text into a shareable file that its own viewer cannot see to review before
        # sharing. It was also the bulk of the export's size — measured, 200 MiB of a
        # 300 MiB page. Add it back only alongside the UI that displays it.
        edges.append(
            {
                "from": src,
                "to": tgt,
                "weight": _num(row.get("weight"), 1),
            }
        )
    return nodes, edges


def _meta_graph(
    membership: dict[str, int],
    relationships: pd.DataFrame,
    title_to_id: dict[str, str],
    legend: list[dict],
) -> tuple[list[dict], list[dict]]:
    """The community meta-graph used above _MAX_NODES entities: one node per
    community, edge weight = number of relationships whose two entities fall in
    different communities. Self-loops (both ends in the same community) aren't
    meta-edges — they're exactly the intra-community structure the community
    already summarizes."""
    nodes = [
        {
            "id": f"c{item['community']}",
            "label": item["title"],
            "community": item["community"],
            "size": item["size"],
            "color": item["color"],
        }
        for item in legend
    ]
    pair_weights: dict[tuple[int, int], int] = {}
    for _, row in relationships.iterrows():
        src = title_to_id.get(row["source"])
        tgt = title_to_id.get(row["target"])
        if src is None or tgt is None:
            continue
        c1, c2 = membership.get(src), membership.get(tgt)
        if c1 is None or c2 is None or c1 == c2:
            continue
        key = (c1, c2) if c1 < c2 else (c2, c1)
        pair_weights[key] = pair_weights.get(key, 0) + 1
    edges = [{"from": f"c{a}", "to": f"c{b}", "weight": w} for (a, b), w in pair_weights.items()]
    return nodes, edges


def _bundled_static_text(relative_path: str) -> str:
    """Reads a vendored asset out of the installed package (same pattern as
    graphrag_adapter._bundled_prompt_text) — not a client-layer import, just package
    data, so this stays inside architecture.md's layering rule."""
    return files("groundly").joinpath(relative_path).read_text(encoding="utf-8")


def _safe_json(data) -> str:
    """One json.dumps blob, escaped so a hostile entity title/description cannot close
    the <script> tag early.

    PRECONDITION, and the thing to re-check before reusing this: the output is only ever
    written into a `<script>` **text node**. That is what makes escaping `<` alone
    sufficient. Move any of this data into an HTML attribute, a `title=`/`data-*` on the
    page shell, or a `<style>` block and `"`, `'` and `>` immediately start mattering --
    this function would silently stop being enough, with no test failing. `<` alone is what matters: the HTML tokenizer only starts
    down the "script end tag" / "comment open" path on a literal `<` (`</script`,
    `<!--`), never on a bare `>` -- so replacing every `<` with `\\u003c` removes every
    literal `<` from the blob and the browser never begins parsing a tag inside the
    script element. `>` is deliberately left alone because it buys nothing: a bare `>`
    cannot start a tag, so escaping it would defend against no reachable attack. It is
    *safe* either way -- `\\u003e` round-trips through JSON.parse back to `>`, so
    citation text like "Use Cases > UC-14" would render identically -- the escape is
    simply unnecessary, and leaving it off keeps the blob greppable. \\u2028/\\u2029 are
    raw newline characters in JS that json.dumps otherwise emits literally, written here
    as escape sequences rather than literal characters so nothing in the toolchain can
    silently eat an invisible line separator out of this source file."""
    blob = json.dumps(data)
    return blob.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def export_graph_html(
    subject_name: str, out_path: Path, *, level: int | None = None
) -> GraphHtmlResult:
    """Write `subject_name`'s graphrag graph as one self-contained HTML file to
    `out_path`. `level=None` colours by the coarsest Leiden level, the one with the
    widest entity coverage (see _resolve_level); the page's own
    level toggle switches between every level without re-running this function."""
    subj = Subject(subject_name)
    graph_dir = subj.root_dir / "graph"
    entities_path = graph_dir / "entities.parquet"
    # Short-circuits before load_manifest() when entities.parquet is missing, so a
    # subject that was never initialized (no manifest.json at all) fails with this
    # message instead of a FileNotFoundError from inside load_manifest().
    if not entities_path.exists() or subj.load_manifest().graphrag.corpus_hash is None:
        raise GraphHtmlError(
            f"no graph is built for {subject_name!r} — run `groundly index --graph` first"
        )

    # All five, not just entities: a build interrupted between workflows leaves some
    # artifacts and not others, and reading those unguarded raised a bare
    # FileNotFoundError straight past the CLI's `except GraphHtmlError` — a raw traceback
    # on precisely the broken-build case this whole feature exists to diagnose.
    missing = [name for name in _REQUIRED_ARTIFACTS if not (graph_dir / name).exists()]
    if missing:
        raise GraphHtmlError(
            f"the graph for {subject_name!r} is incomplete — {', '.join(missing)} "
            f"{'is' if len(missing) == 1 else 'are'} missing from {graph_dir}. A build that "
            "was interrupted leaves a partial graph; re-run `groundly index --graph` to "
            "rebuild it"
        )

    # Decide the view from the parquet FOOTER, before materializing anything. num_rows is
    # metadata — it costs no memory — whereas read_parquet() decompresses the whole column
    # set first and only then meets _MAX_NODES. Measured on a hostile file: a 216 KiB
    # entities.parquet (7,564x expansion) drove peak RSS to ~5.9 GiB and wrote a 1.6 GiB
    # page *while reporting success*. An imported bundle is untrusted input
    # (grounding-and-privacy.md), so the decompressed size is the attacker's choice.
    entity_rows = pq.ParquetFile(entities_path).metadata.num_rows
    if entity_rows == 0:
        raise GraphHtmlError(
            f"the graph for {subject_name!r} has no entities — nothing was extracted from "
            "the corpus, so there is nothing to visualize"
        )
    aggregated = entity_rows > _MAX_NODES
    # _read_bounded, not pd.read_parquet: row count alone is no memory bound — 100 rows of
    # 1 MiB descriptions sit 50x *under* _MAX_NODES and still expand to 100 MiB.
    #
    # Above the cap only the community meta-graph is drawn, and that needs nothing but the
    # id/title join keys — so on the one path where the row count already says the file is
    # huge, the description column is never read at all.
    entities = _read_bounded(
        entities_path, subject_name, _AGGREGATED_ENTITY_COLUMNS if aggregated else None
    )
    relationships = _read_bounded(graph_dir / "relationships.parquet", subject_name)
    communities = _read_bounded(graph_dir / "communities.parquet", subject_name)
    community_reports = _read_bounded(graph_dir / "community_reports.parquet", subject_name)
    text_units = _read_bounded(graph_dir / "text_units.parquet", subject_name)

    chosen_level, levels = _resolve_level(level, communities)
    if aggregated and chosen_level is None:
        raise GraphHtmlError(
            f"{subject_name!r} has {entity_rows} entities (over the {_MAX_NODES} node "
            "cap) but no communities to summarize them into — the graph build produced "
            "entities with no Leiden clustering, so there is no smaller view to render"
        )

    # relationships hold entity TITLES; communities.entity_ids hold entity UUIDs — two
    # different join keys onto the same entity table, both built once up front.
    title_to_id: dict[str, str] = {}
    for _, row in entities.iterrows():
        title_to_id.setdefault(row["title"], row["id"])  # first entity wins a duplicate title

    entity_to_community: dict[int, dict[str, int]] = {}
    for lvl in levels:
        membership: dict[str, int] = {}
        for _, row in communities[communities["level"] == lvl].iterrows():
            cid = int(row["community"])
            for eid in _iter(row["entity_ids"]):
                membership[eid] = cid
        entity_to_community[lvl] = membership

    reports_by_community = {int(row["community"]): row for _, row in community_reports.iterrows()}
    legend_by_level = {
        lvl: _legend_for_level(lvl, communities, reports_by_community) for lvl in levels
    }
    colors_by_level = {
        lvl: {str(item["community"]): item["color"] for item in legend_by_level[lvl]}
        for lvl in levels
    }

    if aggregated:
        nodes, edges = _meta_graph(
            entity_to_community[chosen_level],
            relationships,
            title_to_id,
            legend_by_level[chosen_level],
        )
    else:
        store = SubjectStore(subj.store_db_path)
        citations_by_entity = _entity_citations(entities, text_units, store)
        nodes, edges = _entity_graph(
            entities, relationships, title_to_id, entity_to_community, levels, citations_by_entity
        )

    payload = {
        "subject": subject_name,
        "aggregated": aggregated,
        "total_entities": entity_rows,
        "levels": levels,
        "default_level": chosen_level,
        "legend": {str(lvl): legend_by_level[lvl] for lvl in levels},
        "colors": {str(lvl): colors_by_level[lvl] for lvl in levels},
        "no_community_color": _NO_COMMUNITY_COLOR,
        "nodes": nodes,
        "edges": edges,
    }

    # groundly/assets/, not groundly/web/static/: `web/` is a named *client* layer
    # (architecture.md — nothing below it may depend on it), and while reading package
    # data is not an import, pointing a foundation module at a client directory is an
    # invisible coupling no import graph would ever surface. `assets/` is a bare data
    # directory, the same shape as `prompts/`, which llm/graphrag_adapter.py already reads.
    theme_css = _bundled_static_text("assets/theme.css")
    vis_js = _bundled_static_text("assets/vis-network.min.js")
    html = _HTML_TEMPLATE.format(
        theme_css=theme_css,
        page_css=_PAGE_CSS,
        page_body=_PAGE_BODY,
        vis_js=vis_js,
        data_blob=_safe_json(payload),
        app_js=_APP_JS,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")

    return GraphHtmlResult(
        path=out_path,
        nodes=len(nodes),
        edges=len(edges),
        # Communities at the level actually rendered, not every level summed. len(communities)
        # counts parquet rows across the whole Leiden hierarchy — 20 for test_graph (9 at
        # level 0 + 11 at level 1) — a number the user never sees at once, and one that
        # contradicted the page's own footer. It also makes the aggregated view's defining
        # invariant true rather than accidental: the meta-graph is one node per community
        # *at chosen_level*, so nodes == communities only holds with this count.
        communities=len(legend_by_level[chosen_level]) if chosen_level is not None else 0,
        aggregated=aggregated,
    )


# Everything below is static — no Python-side interpolation inside these blocks, so
# their CSS/JS braces never collide with str.format(). Only _HTML_TEMPLATE itself is
# formatted, with these four blocks (plus the vendored library and the one JSON blob)
# dropped in as opaque text.

_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Groundly knowledge graph</title>
<style>
{theme_css}
{page_css}
</style>
</head>
<body>
{page_body}
<script>
{vis_js}
</script>
<script>
const DATA = {data_blob};
</script>
<script>
{app_js}
</script>
</body>
</html>
"""

_PAGE_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: var(--font-body);
  display: flex;
  height: 100vh;
  overflow: hidden;
}
#graph { flex: 1; background: var(--bg); }
#sidebar {
  width: 280px;
  background: var(--surface);
  border-left: 1px solid var(--divider);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
#search-wrap { padding: 12px; border-bottom: 1px solid var(--divider); position: relative; }
#search {
  width: 100%;
  background: var(--bg);
  border: 1px solid var(--divider);
  color: var(--text);
  padding: 7px 10px;
  border-radius: var(--radius);
  font-size: 13px;
  outline: none;
  font-family: var(--font-body);
}
#search:focus { border-color: var(--primary); }
#search-results {
  max-height: 160px;
  overflow-y: auto;
  padding: 4px 12px;
  border-bottom: 1px solid var(--divider);
  display: none;
}
.search-item {
  padding: 4px 6px;
  cursor: pointer;
  border-radius: var(--radius);
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  border-left: 3px solid transparent;
}
.search-item:hover { background: var(--bg); }
#notice {
  display: none;
  padding: 10px 14px;
  font-size: 12px;
  color: var(--warning);
  border-bottom: 1px solid var(--divider);
  line-height: 1.5;
}
#level-wrap {
  display: none;
  gap: 6px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--divider);
}
.level-btn {
  background: var(--bg);
  color: var(--text-secondary);
  border: 1px solid var(--divider);
  border-radius: var(--radius);
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
  font-family: var(--font-body);
}
.level-btn.active { background: var(--button-primary); color: var(--text); border-color: var(--button-primary); }
#info-panel { padding: 14px; border-bottom: 1px solid var(--divider); overflow-y: auto; max-height: 42vh; }
#info-panel h3, #legend-wrap h3 {
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 8px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-family: var(--font-heading);
}
#info-content { font-size: 13px; line-height: 1.6; }
#info-content .field { margin-bottom: 5px; }
#info-content .field.title { font-size: 14px; font-weight: 600; margin-bottom: 8px; font-family: var(--font-heading); }
#info-content .field b { color: var(--text); }
#info-content .description { margin: 6px 0; color: var(--text-secondary); }
#info-content .empty { color: var(--text-secondary); font-style: italic; }
#info-content .subhead {
  margin-top: 10px;
  font-size: 11px;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.citation { font-family: var(--font-mono); font-size: 11px; color: var(--text-secondary); padding: 2px 0; }
.finding { margin-top: 6px; padding: 6px 8px; background: var(--bg); border-radius: var(--radius); font-size: 12px; }
.finding-title { font-weight: 600; margin-bottom: 2px; }
.neighbor-link {
  display: block;
  padding: 2px 6px;
  margin: 2px 0;
  border-radius: var(--radius);
  cursor: pointer;
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  border-left: 3px solid var(--divider);
}
.neighbor-link:hover { background: var(--bg); }
#neighbors-list { max-height: 160px; overflow-y: auto; margin-top: 4px; }
#legend-wrap { flex: 1; overflow-y: auto; padding: 12px; }
#legend-controls {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--text-secondary);
}
.legend-item { display: flex; align-items: center; gap: 8px; padding: 4px 0; cursor: pointer; border-radius: var(--radius); font-size: 12px; }
.legend-item:hover { background: var(--bg); padding-left: 4px; }
.legend-item.dimmed { opacity: 0.35; }
.legend-dot { width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0; }
.legend-label { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.legend-count { color: var(--text-secondary); font-size: 11px; }
#stats { padding: 10px 14px; border-top: 1px solid var(--divider); font-size: 11px; color: var(--text-secondary); }
"""

_PAGE_BODY = """<div id="graph"></div>
<div id="sidebar">
  <div id="search-wrap">
    <input id="search" type="text" placeholder="Search entities\u2026" autocomplete="off">
    <div id="search-results"></div>
  </div>
  <div id="notice"></div>
  <div id="level-wrap"></div>
  <div id="info-panel">
    <h3>Details</h3>
    <div id="info-content"><span class="empty">Click a node to inspect it</span></div>
  </div>
  <div id="legend-wrap">
    <h3>Communities</h3>
    <div id="legend-controls">
      <label><input type="checkbox" id="select-all-cb" checked> Select all</label>
    </div>
    <div id="legend"></div>
  </div>
  <div id="stats"></div>
</div>"""

_APP_JS = """
document.title = DATA.subject + ' \u2014 knowledge graph';

const nodeById = {};
DATA.nodes.forEach(n => { nodeById[n.id] = n; });

let currentLevel = DATA.default_level;
const hiddenCommunities = new Set();

function degreeSize(d) { return 8 + Math.sqrt(Math.max(d, 0)) * 3; }
function communitySize(s) { return 10 + Math.sqrt(Math.max(s, 1)) * 4; }

function colorFor(node, level) {
  if (DATA.aggregated) return node.color;
  const cid = node.communities[String(level)];
  if (cid === null || cid === undefined) return DATA.no_community_color;
  const levelColors = DATA.colors[String(level)] || {};
  return levelColors[String(cid)] || DATA.no_community_color;
}

function nodeCommunity(node, level) {
  return DATA.aggregated ? node.community : node.communities[String(level)];
}

const container = document.getElementById('graph');
const noticeEl = document.getElementById('notice');
if (DATA.aggregated) {
  noticeEl.textContent = DATA.total_entities + ' entities exceed the visualizer\\'s node '
    + 'cap \u2014 showing ' + DATA.nodes.length + ' communities instead. Click a community for its report.';
  noticeEl.style.display = 'block';
}

const nodesDS = new vis.DataSet(DATA.nodes.map(n => {
  const color = colorFor(n, currentLevel);
  return {
    id: n.id,
    label: n.label,
    title: n.label,
    color: { background: color, border: color, highlight: { background: '#ffffff', border: color } },
    size: DATA.aggregated ? communitySize(n.size) : degreeSize(n.degree),
    font: { size: 0 },
  };
}));

const edgesDS = new vis.DataSet(DATA.edges.map((e, i) => ({
  id: i,
  from: e.from,
  to: e.to,
  width: Math.min(1 + Math.log2(1 + (e.weight || 1)), 6),
  color: { color: 'rgba(255,255,255,0.15)', highlight: 'rgba(255,255,255,0.5)' },
})));

const network = new vis.Network(container, { nodes: nodesDS, edges: edgesDS }, {
  physics: {
    enabled: true,
    solver: 'forceAtlas2Based',
    forceAtlas2Based: {
      gravitationalConstant: -60,
      centralGravity: 0.005,
      springLength: 120,
      springConstant: 0.08,
      damping: 0.4,
      avoidOverlap: 0.8,
    },
    stabilization: { iterations: 200, fit: true },
  },
  interaction: { hover: true, tooltipDelay: 100, hideEdgesOnDrag: true, navigationButtons: false },
  nodes: { shape: 'dot', borderWidth: 1.5 },
  edges: { smooth: { type: 'continuous', roundness: 0.2 } },
});

// Disabling physics once stabilization settles is what keeps a large graph draggable
// instead of endlessly writhing under its own force simulation.
network.once('stabilizationIterationsDone', () => {
  network.setOptions({ physics: { enabled: false } });
});

const infoContent = document.getElementById('info-content');

function clearChildren(el) { while (el.firstChild) el.removeChild(el.firstChild); }

function fieldEl(label, value) {
  const div = document.createElement('div');
  div.className = 'field';
  const b = document.createElement('b');
  b.textContent = label + ': ';
  div.appendChild(b);
  div.appendChild(document.createTextNode(value));
  return div;
}

function titleEl(text) {
  const div = document.createElement('div');
  div.className = 'field title';
  div.textContent = text;
  return div;
}

function descriptionEl(text) {
  const div = document.createElement('div');
  div.className = 'description';
  div.textContent = text;
  return div;
}

function subheadEl(text) {
  const div = document.createElement('div');
  div.className = 'subhead';
  div.textContent = text;
  return div;
}

function showEmpty(message) {
  clearChildren(infoContent);
  const span = document.createElement('span');
  span.className = 'empty';
  span.textContent = message;
  infoContent.appendChild(span);
}

function legendItem(cid, level) {
  return (DATA.legend[String(level)] || []).find(c => c.community === cid) || null;
}

function showEntity(nodeId) {
  const n = nodeById[nodeId];
  if (!n) return;
  clearChildren(infoContent);
  infoContent.appendChild(titleEl(n.label));
  infoContent.appendChild(fieldEl('Type', n.type || 'unknown'));
  const cid = n.communities[String(currentLevel)];
  const community = cid === null || cid === undefined ? null : legendItem(cid, currentLevel);
  infoContent.appendChild(fieldEl('Community', community ? community.title : 'none'));
  infoContent.appendChild(fieldEl('Degree', String(n.degree)));
  if (n.description) infoContent.appendChild(descriptionEl(n.description));

  if (n.citations && n.citations.length) {
    infoContent.appendChild(subheadEl('Citations (' + n.citations.length + ')'));
    n.citations.forEach(c => {
      const div = document.createElement('div');
      div.className = 'citation';
      div.textContent = c;
      infoContent.appendChild(div);
    });
  }

  const neighborIds = network.getConnectedNodes(nodeId);
  if (neighborIds.length) {
    infoContent.appendChild(subheadEl('Neighbours (' + neighborIds.length + ')'));
    const list = document.createElement('div');
    list.id = 'neighbors-list';
    neighborIds.forEach(nid => {
      const nb = nodeById[nid];
      const link = document.createElement('div');
      link.className = 'neighbor-link';
      link.textContent = nb ? nb.label : nid;
      link.style.borderLeftColor = nb ? colorFor(nb, currentLevel) : DATA.no_community_color;
      link.addEventListener('click', () => focusNode(nid));
      list.appendChild(link);
    });
    infoContent.appendChild(list);
  }
}

function showCommunity(cid, level) {
  const item = legendItem(cid, level);
  if (!item) return;
  clearChildren(infoContent);
  infoContent.appendChild(titleEl(item.title));
  infoContent.appendChild(fieldEl('Size', item.size + ' entities'));
  if (item.rank !== null && item.rank !== undefined) infoContent.appendChild(fieldEl('Rank', String(item.rank)));
  if (item.rating_explanation) infoContent.appendChild(descriptionEl(item.rating_explanation));
  if (item.summary) infoContent.appendChild(descriptionEl(item.summary));
  if (item.findings && item.findings.length) {
    infoContent.appendChild(subheadEl('Findings (' + item.findings.length + ')'));
    item.findings.forEach(f => {
      const div = document.createElement('div');
      div.className = 'finding';
      const strong = document.createElement('div');
      strong.className = 'finding-title';
      strong.textContent = f.explanation;
      div.appendChild(strong);
      const p = document.createElement('div');
      p.textContent = f.summary;
      div.appendChild(p);
      infoContent.appendChild(div);
    });
  }
}

function handleNodeClick(nodeId) {
  if (DATA.aggregated) {
    const n = nodeById[nodeId];
    if (n) showCommunity(n.community, currentLevel);
  } else {
    showEntity(nodeId);
  }
}

function focusNode(nodeId) {
  network.focus(nodeId, { scale: 1.4, animation: true });
  network.selectNodes([nodeId]);
  handleNodeClick(nodeId);
}

network.on('click', params => {
  if (params.nodes.length > 0) handleNodeClick(params.nodes[0]);
  else showEmpty('Click a node to inspect it');
});

const searchInput = document.getElementById('search');
const searchResults = document.getElementById('search-results');
searchInput.addEventListener('input', () => {
  const q = searchInput.value.toLowerCase().trim();
  clearChildren(searchResults);
  if (!q) { searchResults.style.display = 'none'; return; }
  const matches = DATA.nodes.filter(n => n.label.toLowerCase().includes(q)).slice(0, 20);
  if (!matches.length) { searchResults.style.display = 'none'; return; }
  searchResults.style.display = 'block';
  matches.forEach(n => {
    const el = document.createElement('div');
    el.className = 'search-item';
    el.textContent = n.label;
    el.style.borderLeftColor = colorFor(n, currentLevel);
    el.addEventListener('click', () => {
      focusNode(n.id);
      searchResults.style.display = 'none';
      searchInput.value = '';
    });
    searchResults.appendChild(el);
  });
});
document.addEventListener('click', e => {
  if (!searchResults.contains(e.target) && e.target !== searchInput) searchResults.style.display = 'none';
});

const legendEl = document.getElementById('legend');
const selectAllCb = document.getElementById('select-all-cb');

function updateSelectAllState() {
  const total = (DATA.legend[String(currentLevel)] || []).length;
  const hidden = hiddenCommunities.size;
  selectAllCb.checked = hidden === 0;
  selectAllCb.indeterminate = hidden > 0 && hidden < total;
}

function setCommunityHidden(cid, hide, item) {
  if (hide) { hiddenCommunities.add(cid); item.classList.add('dimmed'); }
  else { hiddenCommunities.delete(cid); item.classList.remove('dimmed'); }
  const updates = DATA.nodes
    .filter(n => nodeCommunity(n, currentLevel) === cid)
    .map(n => ({ id: n.id, hidden: hide }));
  nodesDS.update(updates);
  updateSelectAllState();
}

function renderLegend(level) {
  clearChildren(legendEl);
  hiddenCommunities.clear();
  (DATA.legend[String(level)] || []).forEach(c => {
    const item = document.createElement('div');
    item.className = 'legend-item';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.className = 'legend-cb';
    cb.checked = true;
    cb.addEventListener('change', e => {
      e.stopPropagation();
      setCommunityHidden(c.community, !cb.checked, item);
    });
    const dot = document.createElement('div');
    dot.className = 'legend-dot';
    dot.style.background = c.color;
    const label = document.createElement('span');
    label.className = 'legend-label';
    label.textContent = c.title;
    const count = document.createElement('span');
    count.className = 'legend-count';
    count.textContent = String(c.size);
    item.appendChild(cb);
    item.appendChild(dot);
    item.appendChild(label);
    item.appendChild(count);
    item.addEventListener('click', e => {
      if (e.target === cb) return;
      showCommunity(c.community, level);
    });
    legendEl.appendChild(item);
  });
  updateSelectAllState();
  document.getElementById('stats').textContent =
    DATA.nodes.length + ' nodes \\u00b7 ' + DATA.edges.length + ' edges \\u00b7 '
    + (DATA.legend[String(level)] || []).length + ' communities';
}

selectAllCb.addEventListener('change', () => {
  const hide = !selectAllCb.checked;
  document.querySelectorAll('.legend-item').forEach((item, i) => {
    const c = (DATA.legend[String(currentLevel)] || [])[i];
    if (!c) return;
    hide ? item.classList.add('dimmed') : item.classList.remove('dimmed');
    const cb = item.querySelector('.legend-cb');
    if (cb) cb.checked = !hide;
  });
  hiddenCommunities.clear();
  if (hide) (DATA.legend[String(currentLevel)] || []).forEach(c => hiddenCommunities.add(c.community));
  nodesDS.update(DATA.nodes.map(n => ({ id: n.id, hidden: hide })));
  updateSelectAllState();
});

renderLegend(currentLevel);

// Level toggle recolours existing entity nodes and rebuilds the legend for the new
// level — cheap, since every node already carries its community for every level. Not
// offered in aggregated mode: there the nodes ARE the communities of one fixed level,
// so "switching level" would mean swapping the whole graph, not just its colours.
const levelWrap = document.getElementById('level-wrap');
if (!DATA.aggregated && DATA.levels.length > 1) {
  levelWrap.style.display = 'flex';
  DATA.levels.forEach(lvl => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = 'Level ' + lvl;
    btn.className = 'level-btn' + (lvl === currentLevel ? ' active' : '');
    btn.addEventListener('click', () => setLevel(lvl));
    levelWrap.appendChild(btn);
  });
}

function setLevel(lvl) {
  currentLevel = lvl;
  levelWrap.querySelectorAll('.level-btn').forEach((btn, i) => {
    btn.classList.toggle('active', DATA.levels[i] === lvl);
  });
  nodesDS.update(DATA.nodes.map(n => {
    const color = colorFor(n, lvl);
    return { id: n.id, color: { background: color, border: color, highlight: { background: '#ffffff', border: color } } };
  }));
  renderLegend(lvl);
  showEmpty('Click a node to inspect it');
}
"""
