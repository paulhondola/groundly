"""groundly/core/graph_html.py: `groundly export-graph` writes ONE self-contained HTML
file visualizing a subject's knowledge graph. Fixtures write small parquet frames with
pandas; no test runs a real graphrag pipeline.

Fixtures honour a real build's join asymmetry (do not "simplify" it away; each half is a
different join key):
  - communities.entity_ids holds entity UUIDs, matching entities.id
  - relationships.source/.target hold entity TITLES, matching entities.title
  - text_units.document_id holds the Groundly chunk_id as a STRING, joining to store.db's
    chunks.id
"""

import base64
import hashlib
import re
import shutil
import subprocess
import uuid
from importlib.resources import files

import pandas as pd
import pytest

from groundly.core.graph_html import _MAX_NODES, GraphHtmlError, export_graph_html
from groundly.core.manifest import EMBEDDING_DIM
from groundly.core.store import SubjectStore
from groundly.core.subject import Subject
from groundly.ingestion.extract import ChunkData
from tests.conftest import init_subject

_ENTITY_COLUMNS = [
    "id",
    "human_readable_id",
    "title",
    "type",
    "description",
    "text_unit_ids",
    "frequency",
    "degree",
]
_RELATIONSHIP_COLUMNS = [
    "id",
    "human_readable_id",
    "source",
    "target",
    "description",
    "weight",
    "combined_degree",
    "text_unit_ids",
]
_COMMUNITY_COLUMNS = [
    "id",
    "human_readable_id",
    "community",
    "level",
    "parent",
    "children",
    "title",
    "entity_ids",
    "relationship_ids",
    "text_unit_ids",
    "period",
    "size",
]
_REPORT_COLUMNS = [
    "id",
    "human_readable_id",
    "community",
    "level",
    "parent",
    "children",
    "title",
    "summary",
    "full_content",
    "rank",
    "rating_explanation",
    "findings",
    "full_content_json",
    "period",
    "size",
]
_TEXT_UNIT_COLUMNS = [
    "id",
    "human_readable_id",
    "text",
    "n_tokens",
    "document_id",
    "entity_ids",
    "relationship_ids",
    "covariate_ids",
]
_DOCUMENT_COLUMNS = [
    "id",
    "human_readable_id",
    "title",
    "text",
    "text_unit_ids",
    "creation_date",
    "raw_data",
]


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


@pytest.fixture
def subj():
    init_subject("TEST")
    return Subject("TEST")


@pytest.fixture
def store(subj):
    return SubjectStore(subj.store_db_path)


# --- fixture builders: one row-dict factory per parquet file, matching the real schema --


def _entity_row(title: str, *, description: str = "", text_unit_ids=None, hrid=0) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "human_readable_id": hrid,
        "title": title,
        "type": "concept",
        "description": description,
        "text_unit_ids": list(text_unit_ids or []),
        "frequency": 1,
        "degree": 1,
    }


def _relationship_row(source_title: str, target_title: str, *, description="", hrid=0) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "human_readable_id": hrid,
        "source": source_title,
        "target": target_title,
        "description": description,
        "weight": 1.0,
        "combined_degree": 2,
        "text_unit_ids": [],
    }


def _community_row(community: int, level: int, title: str, entity_ids: list[str]) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "human_readable_id": community,
        "community": community,
        "level": level,
        "parent": -1,
        "children": [],
        "title": title,
        "entity_ids": list(entity_ids),
        "relationship_ids": [],
        "text_unit_ids": [],
        "period": "2026-01-01",
        "size": len(entity_ids),
    }


def _report_row(community: int, level: int, title: str, summary: str) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "human_readable_id": community,
        "community": community,
        "level": level,
        "parent": -1,
        "children": [],
        "title": title,
        "summary": summary,
        "full_content": summary,
        "rank": 1.0,
        "rating_explanation": "",
        "findings": [{"explanation": summary, "summary": summary}],
        "full_content_json": "{}",
        "period": "2026-01-01",
        "size": 1,
    }


def _text_unit_row(tu_id: str, chunk_id: int, text: str = "chunk text") -> dict:
    return {
        "id": tu_id,
        "human_readable_id": 0,
        "text": text,
        "n_tokens": len(text.split()),
        "document_id": str(chunk_id),  # Groundly chunk_id, joins store.db chunks.id
        "entity_ids": [],
        "relationship_ids": [],
        "covariate_ids": [],
    }


def _frame(rows: list[dict], columns: list[str]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame({c: [] for c in columns})
    return pd.DataFrame(rows, columns=columns)


def _write_graph(
    subj: Subject,
    *,
    entities: list[dict],
    relationships: list[dict] | None = None,
    communities: list[dict] | None = None,
    community_reports: list[dict] | None = None,
    text_units: list[dict] | None = None,
    documents: list[dict] | None = None,
) -> None:
    graph_dir = subj.root_dir / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    _frame(entities, _ENTITY_COLUMNS).to_parquet(graph_dir / "entities.parquet")
    _frame(relationships or [], _RELATIONSHIP_COLUMNS).to_parquet(
        graph_dir / "relationships.parquet"
    )
    _frame(communities or [], _COMMUNITY_COLUMNS).to_parquet(graph_dir / "communities.parquet")
    _frame(community_reports or [], _REPORT_COLUMNS).to_parquet(
        graph_dir / "community_reports.parquet"
    )
    _frame(text_units or [], _TEXT_UNIT_COLUMNS).to_parquet(graph_dir / "text_units.parquet")
    _frame(documents or [], _DOCUMENT_COLUMNS).to_parquet(graph_dir / "documents.parquet")

    # A completed build stamps the manifest; graph_html.py's own export gate checks
    # corpus_hash rather than directory presence, so fixtures must stamp it too.
    manifest = subj.load_manifest()
    manifest.graphrag.corpus_hash = "stamped-for-test"
    subj.save_manifest(manifest)


def _add_chunk(store: SubjectStore, filename: str, sha256: str, text: str, *, page, heading_path):
    """Indexes one material with one chunk and returns its store.db chunk id (ids are
    assigned in insertion order starting at 1, so callers can predict them)."""
    chunk = ChunkData(text, heading_path, page, len(text.split()))
    dense = [[0.1] * EMBEDDING_DIM]
    sparse = [{1: 0.5}]
    store.add_indexed(filename, sha256, page, [chunk], zip(dense, sparse))
    return store.all_chunks()[-1]["chunk_id"]


# --- XSS containment: the one that matters most ------------------------------------------


def test_entity_text_cannot_break_out_of_the_script_block(subj, store):
    """Entity/relationship text comes straight from course material (layer 3), and
    `groundly import` is the trust boundary: a hostile bundle can carry a title built to
    close the <script> tag the graph JSON sits in and run arbitrary JS when the page opens.
    The escape has to live *inside* the JSON string itself (`<` -> `\\u003c`)."""
    xss_title = "</script><img src=x onerror=alert(1)>"
    attacker = _entity_row(xss_title, description="attacker entity", hrid=0)
    comment_open = _entity_row(
        "Comment Open", description="before <!-- unterminated comment", hrid=1
    )
    comment_close = _entity_row(
        "Comment Close", description="terminated --> comment-close-marker", hrid=2
    )
    entities = [attacker, comment_open, comment_close]
    community = _community_row(0, 0, "Community 0", [e["id"] for e in entities])
    report = _report_row(0, 0, "Report", "summary")

    _write_graph(subj, entities=entities, communities=[community], community_reports=[report])

    result = export_graph_html("TEST", subj.root_dir / "out.html")
    html = result.path.read_text()

    # the raw payload must never appear verbatim — as live markup or as a substring that
    # would terminate the <script> block early
    assert xss_title not in html
    assert "<!-- unterminated comment" not in html
    # content past the comment marker must survive intact — proves the escape didn't
    # truncate or otherwise corrupt the string, it just neutralized the dangerous chars
    assert "comment-close-marker" in html
    # The load-bearing assertion, stated as the structural property: no <script> body may
    # contain a premature "</script", the only way the element can end early. Pinning one
    # spelling of the escape would fail a future, equally correct escaping.
    bodies = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)
    assert bodies, "no <script> block found — the embedded data blob is missing"
    for body in bodies:
        assert not re.search(r"(?i)</script", body)


# --- the page's own JS actually parses -------------------------------------------------------


def test_app_js_parses(subj, store):
    """Every other test here inspects the page as *text* and none executes it, so a page
    whose script cannot parse would pass them all. Skipped where node is unavailable."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available to parse-check the emitted JS")

    entity = _entity_row("Solo Entity", description="d")
    _write_graph(
        subj,
        entities=[entity],
        communities=[_community_row(0, 0, "Community 0", [entity["id"]])],
        community_reports=[_report_row(0, 0, "Report", "summary")],
    )
    html = export_graph_html("TEST", subj.root_dir / "out.html").path.read_text()

    # the third <script> is the app; the first is vendored vis-network, the second the data
    app_js = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)[2]
    checked = subj.root_dir / "app.js"
    checked.write_text(app_js, encoding="utf-8")
    result = subprocess.run([node, "--check", str(checked)], capture_output=True, text=True)

    assert result.returncode == 0, f"emitted page JS does not parse:\n{result.stderr}"


# --- the vendored blob ---------------------------------------------------------------------


def test_vendored_vis_network_matches_its_recorded_hash():
    """A 700 KB minified third-party file is unreviewable in a diff, so a substituted build
    would pass review on the strength of its filename. `groundly/assets/VENDORED.md` records
    the SHA-384; this assertion is what enforces it. On an intentional upgrade both change
    in the same commit."""
    blob = files("groundly").joinpath("assets/vis-network.min.js").read_bytes()
    digest = base64.b64encode(hashlib.sha384(blob).digest()).decode()

    assert digest == "Ux6phic9PEHJ38YtrijhkzyJ8yQlH8i/+buBR8s3mAZOJrP1gwyvAcIYl3GWtpX1"
    assert len(blob) == 702_611
    recorded = files("groundly").joinpath("assets/VENDORED.md").read_text(encoding="utf-8")
    assert digest in recorded, "VENDORED.md must record the hash the wheel actually ships"


# --- no egress -----------------------------------------------------------------------------

_URL_IN_MARKUP_ATTR = re.compile(r'(?:src|href)\s*=\s*["\']https?://', re.IGNORECASE)


def test_page_references_no_external_url(subj, store):
    """The privacy rule permits no CDN: vis-network is vendored so an exported page never
    phones a CDN on view, disclosing the student's IP and viewing time. A URL inside the
    vendored library's license comment is expected, so the assertion is scoped to markup
    attributes (src=/href=), not the whole file."""
    entity = _entity_row("Solo Entity", description="d")
    community = _community_row(0, 0, "Community 0", [entity["id"]])
    report = _report_row(0, 0, "Report", "summary")

    _write_graph(subj, entities=[entity], communities=[community], community_reports=[report])

    result = export_graph_html("TEST", subj.root_dir / "out.html")
    html = result.path.read_text()

    assert not _URL_IN_MARKUP_ATTR.search(html)


# --- community labels come from the report, not the mechanical title ----------------------


def test_sidebar_shows_the_reports_human_label_not_the_raw_community_title(subj, store):
    """communities.title is graphrag's mechanical "Community N"; the report's own title is
    the human-readable label ("Retrieval Pipeline") a student actually wants to read."""
    entity = _entity_row("Some Entity", description="d")
    community = _community_row(0, 0, "Community 0", [entity["id"]])
    report = _report_row(0, 0, "Retrieval Pipeline", "summary")

    _write_graph(subj, entities=[entity], communities=[community], community_reports=[report])

    result = export_graph_html("TEST", subj.root_dir / "out.html")
    html = result.path.read_text()

    assert "Retrieval Pipeline" in html
    assert "Community 0" not in html


# --- citations resolve, including the page-less case ---------------------------------------


def test_citations_resolve_and_the_pageless_case_falls_back_to_the_heading(subj, store):
    """Citation targets must resolve through store.db (chunk id -> filename/page/
    heading_path), not graphrag's documents.title, which reads "knowledge-base.md#pNone"
    for a page-less chunk."""
    chunk_pdf = _add_chunk(store, "lec.pdf", "a" * 64, "slides text", page=3, heading_path=None)
    chunk_md = _add_chunk(
        store, "notes.md", "b" * 64, "markdown text", page=None, heading_path="Intro > Overview"
    )

    entity = _entity_row("Cited Entity", description="d", text_unit_ids=["tu-0", "tu-1"])
    community = _community_row(0, 0, "Community 0", [entity["id"]])
    report = _report_row(0, 0, "Report", "summary")
    text_units = [
        _text_unit_row("tu-0", chunk_pdf),
        _text_unit_row("tu-1", chunk_md),
    ]

    _write_graph(
        subj,
        entities=[entity],
        communities=[community],
        community_reports=[report],
        text_units=text_units,
    )

    result = export_graph_html("TEST", subj.root_dir / "out.html")
    html = result.path.read_text()

    assert "lec.pdf, p.3" in html
    assert "Intro > Overview" in html
    assert "pNone" not in html
    assert "p.None" not in html


# --- scale guard -----------------------------------------------------------------------------


def test_graphs_above_the_node_cap_render_the_community_meta_graph(subj, store):
    """Above `_MAX_NODES` a force-directed page is an unusable hairball, so the generator
    must fall back to one node per community. Imports `_MAX_NODES` so the test tracks the
    real cap."""
    n_communities = 10
    per_community = (_MAX_NODES // n_communities) + 1  # guarantees > _MAX_NODES entities total
    entities: list[dict] = []
    communities: list[dict] = []
    reports: list[dict] = []
    for c in range(n_communities):
        members = [
            _entity_row(f"Entity {c}-{i}", description="d", hrid=c * per_community + i)
            for i in range(per_community)
        ]
        entities.extend(members)
        communities.append(_community_row(c, 0, f"Community {c}", [m["id"] for m in members]))
        reports.append(_report_row(c, 0, f"Report {c}", "summary"))

    _write_graph(subj, entities=entities, communities=communities, community_reports=reports)

    result = export_graph_html("TEST", subj.root_dir / "out.html")

    assert result.aggregated is True
    assert result.nodes == result.communities == n_communities


# --- missing graph names the cause ------------------------------------------------------------


def test_missing_graph_names_the_cause(subj):
    """No graph/ directory at all is the most basic "cannot visualize" case. It must
    surface as a named GraphHtmlError, not a raw KeyError/FileNotFoundError from a parquet
    read that assumed the directory existed."""
    with pytest.raises(GraphHtmlError) as exc_info:
        export_graph_html("TEST", subj.root_dir / "out.html")

    assert not isinstance(exc_info.value, (KeyError, FileNotFoundError))
    message = str(exc_info.value)
    assert message  # non-empty: a named cause, not a bare exception
    assert "graph" in message.lower()


# --- result counts are truthful -----------------------------------------------------------


def test_result_counts_match_the_fixture(subj, store):
    e1 = _entity_row("Entity One", description="d1")
    e2 = _entity_row("Entity Two", description="d2")
    e3 = _entity_row("Entity Three", description="d3")
    entities = [e1, e2, e3]
    relationships = [
        _relationship_row("Entity One", "Entity Two", description="r1"),
        _relationship_row("Entity Two", "Entity Three", description="r2"),
    ]
    communities = [
        _community_row(0, 0, "Community 0", [e1["id"], e2["id"]]),
        _community_row(1, 0, "Community 1", [e3["id"]]),
    ]
    reports = [
        _report_row(0, 0, "Report 0", "summary0"),
        _report_row(1, 0, "Report 1", "summary1"),
    ]

    _write_graph(
        subj,
        entities=entities,
        relationships=relationships,
        communities=communities,
        community_reports=reports,
    )

    result = export_graph_html("TEST", subj.root_dir / "out.html")

    assert (result.nodes, result.edges, result.communities) == (3, 2, 2)
