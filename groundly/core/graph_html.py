"""Self-contained HTML visualization of a subject's knowledge graph (`groundly
export-graph`): entities coloured by Leiden community, with community reports and
citations in a sidebar.

Nothing may leave the machine, so there is no CDN: vendored vis-network and the page's
`assets/` files are inlined at generation time. They are real files, not string literals,
so their escapes pass through one parser. Entity text comes from course material, which
an imported bundle controls (layer 3), so: (1) all graph data is embedded as one escaped
`json.dumps` blob, never concatenated into markup; (2) the JS reaches the DOM only via
textContent/createElement, never innerHTML; (3) `data_blob` is substituted last, so no
later `.replace()` rescans course text for `{{...}}` tokens.
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

# Above this many entities the force-directed page stops being usable, so one node per
# community (the "aggregated" view) is rendered instead.
_MAX_NODES = 5000

# Per-field display cap (see _text): _MAX_NODES bounds how many things are drawn, this
# bounds how big each attacker-controlled field may be.
_MAX_FIELD_CHARS = 8000

# Ceiling on what one parquet artifact may expand to in memory (see _read_bounded).
# Generous: a real subject's whole graph/ is a few MB.
_MAX_ARTIFACT_BYTES = 512 * 1024 * 1024

# Rows per streaming batch in _read_bounded. Row size is attacker-controlled, so a small
# batch keeps the overshoot past the limit small before the check fires.
_READ_BATCH_ROWS = 512

# All the aggregated view needs, so an inflated `description` column never reaches the
# heap on the path where the entity count already says the file is huge.
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

# Tableau10 categorical palette.
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
    """Markdown chunks have page=None, so fall back to the heading path; never render a
    "#pNone" citation."""
    if page is not None:
        return f"{filename}, p.{page}"
    if heading_path:
        return f"{filename} \u203a {heading_path}"
    return filename


def _num(value, default=0):
    """A parquet cell as a JSON-safe number: numpy scalars become Python numbers, and NaN
    (which json.dumps emits as invalid JSON) becomes `default`."""
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
    """A parquet cell as display text, capped at _MAX_FIELD_CHARS.

    The cap is a resource bound: field length is attacker-controlled in an imported bundle,
    and a few MiB-sized descriptions sit far under _MAX_NODES. This is the one choke point
    every such string passes through; keep it that way rather than capping per call site."""
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
    """`pd.read_parquet` with a ceiling on what it materializes.

    An imported bundle's decompressed size is the sender's choice, and parquet inflates
    repetitive text enormously. The footer cannot bound it: `total_byte_size` is the
    encoded size, which dictionary encoding shrinks for exactly that attack. So stream
    batches and stop once their real nbytes cross the limit; peak memory is the limit plus
    one batch, whatever the encoding."""
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
    """`level=None` means the coarsest level (0). Leiden only subdivides communities large
    enough to split, so finer levels cover fewer entities and can leave most of the graph
    grey; the page's toggle still reaches every level. A missing explicit level is an
    error, not a silent fallback to an empty legend."""
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
                # label. community_reports.title is the LLM-generated one.
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
    """Entity -> text units -> `document_id`, which build_graph sets to str(chunk_id), then
    one store.db lookup for every chunk a rendered entity cites."""
    # A list per text-unit id: text-unit ids are content hashes, so byte-identical chunks on
    # different pages collide onto one id. An index lookup would return a Series there, and
    # the except below would silently drop those entities' citations.
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
        # No `description`: the page never shows it, so it would put layer-3 course text into
        # a shareable file where its viewer cannot review it. Add it only with UI that does.
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
    """The community meta-graph used above _MAX_NODES entities: one node per community,
    edge weight = relationships crossing between two communities. Intra-community edges
    are dropped; the community's summary already covers them."""
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
    """A vendored asset read from package data, not from a client-layer module."""
    return files("groundly").joinpath(relative_path).read_text(encoding="utf-8")


def _safe_json(data) -> str:
    """One json.dumps blob that a hostile entity string cannot use to close <script> early.

    PRECONDITION: the output is only ever written into a `<script>` **text node**. There,
    escaping `<` alone is sufficient, because the tokenizer can only leave script data on a
    literal `<` (`</script`, `<!--`). In an attribute or `<style>`, `"`, `'` and `>` would
    matter too and this would silently stop being enough. U+2028/U+2029 are escaped as
    well, written as escape sequences so no tool can strip them from this file."""
    blob = json.dumps(data)
    return blob.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def export_graph_html(
    subject_name: str, out_path: Path, *, level: int | None = None
) -> GraphHtmlResult:
    """Write `subject_name`'s graph as one self-contained HTML file. `level=None` colours by
    the coarsest Leiden level (see _resolve_level); the page toggles between levels."""
    subj = Subject(subject_name)
    graph_dir = subj.root_dir / "graph"
    entities_path = graph_dir / "entities.parquet"
    # Checked before load_manifest(), so a never-initialized subject gets this message
    # rather than a FileNotFoundError.
    if not entities_path.exists() or subj.load_manifest().graphrag.corpus_hash is None:
        raise GraphHtmlError(
            f"no graph is built for {subject_name!r} — run `groundly index --graph` first"
        )

    # All five, not just entities: a build interrupted between workflows leaves some
    # artifacts and not others, and reading those unguarded would raise a bare
    # FileNotFoundError straight past the CLI's `except GraphHtmlError`.
    missing = [name for name in _REQUIRED_ARTIFACTS if not (graph_dir / name).exists()]
    if missing:
        raise GraphHtmlError(
            f"the graph for {subject_name!r} is incomplete — {', '.join(missing)} "
            f"{'is' if len(missing) == 1 else 'are'} missing from {graph_dir}. A build that "
            "was interrupted leaves a partial graph; re-run `groundly index --graph` to "
            "rebuild it"
        )

    # Choose the view from the footer's row count, which costs no memory, before reading
    # column data: an imported bundle's decompressed size is the attacker's choice.
    entity_rows = pq.ParquetFile(entities_path).metadata.num_rows
    if entity_rows == 0:
        raise GraphHtmlError(
            f"the graph for {subject_name!r} has no entities — nothing was extracted from "
            "the corpus, so there is nothing to visualize"
        )
    aggregated = entity_rows > _MAX_NODES
    # Row count alone bounds no memory (100 rows of 1 MiB descriptions sit far under the
    # cap), so every read is bounded, and the aggregated view never reads `description`.
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

    # data_blob goes last: it carries course text, and a later .replace() would rescan it
    # for "{{placeholder}}"-shaped substrings.
    html = _bundled_static_text("assets/graph.html")
    for placeholder, value in {
        "theme_css": _bundled_static_text("assets/theme.css"),
        "page_css": _bundled_static_text("assets/graph.css"),
        "page_body": _bundled_static_text("assets/graph_body.html"),
        "vis_js": _bundled_static_text("assets/vis-network.min.js"),
        "app_js": _bundled_static_text("assets/graph.js"),
        "data_blob": _safe_json(payload),
    }.items():
        html = html.replace("{{" + placeholder + "}}", value)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")

    return GraphHtmlResult(
        path=out_path,
        nodes=len(nodes),
        edges=len(edges),
        # Communities at the rendered level, not the whole hierarchy: that is what the page
        # shows, and it keeps nodes == communities in the aggregated view.
        communities=len(legend_by_level[chosen_level]) if chosen_level is not None else 0,
        aggregated=aggregated,
    )
