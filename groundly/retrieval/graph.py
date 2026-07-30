"""Arm 2 (graph backend): MS graphrag local/global search over a subject's
pre-built graph (parquet artifacts + LanceDB store under `<subject>/graph/`,
built by `groundly/ingestion/graph.py`). Local search is entity-anchored
(multi-hop); global search is community-summary synthesis. Both share the
`BaseRetriever` "four arms, one interface" gate and the vector arm's
`NodeWithScore` metadata contract (chunk_id/filename/page/heading_path) —
nothing downstream special-cases this arm.

Citation resolution for local search: graphrag's local search context (the
"sources" key of its context_data) carries each cited text unit's *positional*
index into the `text_units` DataFrame we pass in (graphrag's own `short_id`
scheme — not `document_id`), so citation ids resolve by looking that position
up in the same DataFrame and reading its `document_id` column, which equals
Groundly's chunk_id directly (guaranteed 1:1 by the ingestion batch builder).

Citation resolution for global search is the open-risk join flagged in the P5
design spec: community reports have no page and are never citation targets, so
we join the reports actually used as the map-reduce's context back to their
member entities (via graphrag's own `read_indexer_entities`, same community
join it uses internally) and those entities' contributing text units, then
resolve text units to chunk_ids the same way as local search.

Known gap: the synthesis LLM call graphrag makes internally inside `local_search`/
`global_search` is NOT traced/metered by Groundly — graphrag's own LiteLLM client
doesn't report usage back through our `llm/` layer, so this call is invisible to the
traces table (a framework-boundary limitation, not something this module fixes).
"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from graphrag.api.query import global_search, local_search
from graphrag.config.models.graph_rag_config import GraphRagConfig
from graphrag.query.indexer_adapters import read_indexer_entities
from graphrag_llm.config import ModelConfig
from graphrag_vectors import VectorStoreConfig
from llama_index.core.callbacks import CallbackManager
from llama_index.core.retrievers import BaseRetriever
from llama_index.core.schema import NodeWithScore, QueryBundle, TextNode

from groundly.core.manifest import EMBEDDING_DIM
from groundly.core.store import SubjectStore
from groundly.core.subject import Subject
from groundly.llm.graphrag_adapter import (
    BGE_M3_EMBEDDING_TYPE,
    allow_nonstandard_service_tier,
    completion_model_config,
    register_bge_m3_embedding,
)

logger = logging.getLogger(__name__)

# Matches graphrag CLI's own default (`--community-level`, graphrag/cli/main.py). This
# is a level *ceiling*, not an exact match (`df[df.level <= community_level]` —
# confirmed via graphrag's `_filter_under_community_level`): build and query disagreeing
# here is harmless, a smaller value just means less breadth, never empty/broken results.
COMMUNITY_LEVEL = 2
RESPONSE_TYPE = "multiple paragraphs"
# Unlike COMMUNITY_LEVEL above, query and build MUST agree on these — they're the keys
# graphrag looks up completion_models/embedding_models by; a mismatch fails the lookup.
_COMPLETION_MODEL_ID = "default_completion_model"
_EMBEDDING_MODEL_ID = "default_embedding_model"


class GraphNotBuiltError(Exception):
    """Raised by graph arms whenever `<subject>/graph/` doesn't exist yet."""

    def __init__(self, message: str = "run `groundly index --graph` first") -> None:
        super().__init__(f"graph not built for this subject — {message}")


@dataclass
class _GraphArtifacts:
    """A subject's graphrag build artifacts, loaded once per retriever instance."""

    config: GraphRagConfig
    entities: pd.DataFrame
    communities: pd.DataFrame
    community_reports: pd.DataFrame
    text_units: pd.DataFrame
    relationships: pd.DataFrame


def _load_artifacts(graph_dir: Path) -> _GraphArtifacts:
    """Read the parquet artifacts graphrag's build wrote, plus a query-time config
    reusing the `extraction` provider (local/global search both do their own
    synthesis LLM call — same call class as the graph build)."""
    # Here rather than in either retriever: this is the one place that builds the
    # completion config, so both arms are covered and a future third one can't miss it.
    # Global search was previously left out, so a provider returning a non-OpenAI
    # service_tier still failed every synthesis call on that arm.
    allow_nonstandard_service_tier()
    config = GraphRagConfig(
        completion_models={_COMPLETION_MODEL_ID: completion_model_config()},
        embedding_models={
            _EMBEDDING_MODEL_ID: ModelConfig(
                type=BGE_M3_EMBEDDING_TYPE,
                model_provider=BGE_M3_EMBEDDING_TYPE,
                model="bge-m3",
            )
        },
        vector_store=VectorStoreConfig(
            db_uri=str(graph_dir / "lancedb"), vector_size=EMBEDDING_DIM
        ),
    )
    return _GraphArtifacts(
        config=config,
        entities=pd.read_parquet(graph_dir / "entities.parquet"),
        communities=pd.read_parquet(graph_dir / "communities.parquet"),
        community_reports=pd.read_parquet(graph_dir / "community_reports.parquet"),
        text_units=pd.read_parquet(graph_dir / "text_units.parquet"),
        relationships=pd.read_parquet(graph_dir / "relationships.parquet"),
    )


def _nodes_from_chunk_ids(store: SubjectStore, chunk_ids: list[int]) -> list[NodeWithScore]:
    """Resolve chunk ids to the shared metadata contract, in the given order (best
    first) — same shape `VectorRetriever` produces, so citation resolution and
    prompt assembly never special-case the graph arm."""
    if not chunk_ids:
        return []
    details = {row["chunk_id"]: row for row in store.chunk_details(chunk_ids)}
    nodes = []
    for rank, chunk_id in enumerate(chunk_ids):
        row = details.get(chunk_id)
        if row is None:  # entity/text-unit pointed at a chunk since removed — skip
            logger.debug("chunk %s vanished between fusion and detail lookup", chunk_id)
            continue
        node = TextNode(
            text=row["text"],
            id_=str(chunk_id),
            metadata={
                "chunk_id": chunk_id,
                "filename": row["filename"],
                "page": row["page"],
                "heading_path": row["heading_path"],
            },
        )
        nodes.append(NodeWithScore(node=node, score=1.0 / (rank + 1)))
    return nodes


class _GraphRetrieverBase(BaseRetriever):
    """Shared subject/artifact plumbing for the two graph arms. `self.path` records
    which stages ran (trace logging, mirrors `VectorRetriever`); `self.communities`
    is set by `GraphGlobalRetriever` only, exposed for `overview()` (agents/study_modes.py)
    to name its constituent communities."""

    def __init__(self, subject: str) -> None:
        super().__init__(callback_manager=CallbackManager([]))
        self.subject = subject
        self._subj = Subject(subject)
        self.store = SubjectStore(self._subj.store_db_path)
        self.path: list[str] = []
        self.communities: list[dict] = []
        self._artifacts: _GraphArtifacts | None = None

    @property
    def graph_dir(self) -> Path:
        return self._subj.root_dir / "graph"

    def _require_graph(self) -> None:
        # Directory presence is not proof of a usable graph. A build that failed the
        # extraction gate — or was interrupted mid-run — leaves partial parquet behind
        # on purpose, so graphrag's LLM cache survives for the retry. `corpus_hash` is
        # written only by a build that passed every check, so it is the honest record
        # of "there is a graph here", and the same field graph_is_stale reads.
        if not self.graph_dir.exists() or self._subj.load_manifest().graphrag.corpus_hash is None:
            raise GraphNotBuiltError()

    @property
    def artifacts(self) -> _GraphArtifacts:
        if self._artifacts is None:
            self._artifacts = _load_artifacts(self.graph_dir)
        return self._artifacts


class GraphLocalRetriever(_GraphRetrieverBase):
    """Entity-anchored local search — multi-hop queries."""

    def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        self._require_graph()
        register_bge_m3_embedding()  # local search embeds the query to find entities
        artifacts = self.artifacts  # _load_artifacts widens service_tier for both arms

        _response, context_data = asyncio.run(
            local_search(
                config=artifacts.config,
                entities=artifacts.entities,
                communities=artifacts.communities,
                community_reports=artifacts.community_reports,
                text_units=artifacts.text_units,
                relationships=artifacts.relationships,
                covariates=None,
                community_level=COMMUNITY_LEVEL,
                response_type=RESPONSE_TYPE,
                query=query_bundle.query_str,
            )
        )
        self.path = ["graphrag-local", "entity-search"]

        sources = (context_data or {}).get("sources")
        chunk_ids: list[int] = []
        if sources is not None and not sources.empty:
            text_units = artifacts.text_units
            for position in sources["id"]:
                idx = int(position)
                if 0 <= idx < len(text_units):
                    document_id = text_units.iloc[idx]["document_id"]
                    if document_id is not None:
                        chunk_ids.append(int(document_id))
        self.path.append("text-unit-resolve")
        logger.debug(
            "local search: %d source(s) resolved to %d chunk id(s), path=%s",
            0 if sources is None else len(sources),
            len(chunk_ids),
            self.path,
        )
        return _nodes_from_chunk_ids(self.store, chunk_ids)


class GraphGlobalRetriever(_GraphRetrieverBase):
    """Community-summary global search — synthesis queries. Citations resolve via
    the community reports actually used as context -> their member entities ->
    those entities' contributing text units -> chunk_ids (see module docstring —
    this join is the P5 design spec's flagged open risk)."""

    def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        self._require_graph()
        artifacts = self.artifacts

        _response, context_data = asyncio.run(
            global_search(
                config=artifacts.config,
                entities=artifacts.entities,
                communities=artifacts.communities,
                community_reports=artifacts.community_reports,
                community_level=COMMUNITY_LEVEL,
                dynamic_community_selection=False,
                response_type=RESPONSE_TYPE,
                query=query_bundle.query_str,
            )
        )
        self.path = ["graphrag-global", "community-search"]

        reports = (context_data or {}).get("reports")
        self.communities = []
        if reports is None or reports.empty:
            self.path.append("entity-resolve")
            self.path.append("text-unit-resolve")
            logger.debug("global search: no community reports matched, path=%s", self.path)
            return []

        self.communities = [
            {"id": str(row["id"]), "title": str(row.get("title", ""))}
            for _, row in reports.iterrows()
        ]
        used_community_ids = {c["id"] for c in self.communities}

        entities = read_indexer_entities(artifacts.entities, artifacts.communities, COMMUNITY_LEVEL)
        text_unit_ids = {
            text_unit_id
            for entity in entities
            if entity.community_ids and used_community_ids & set(entity.community_ids)
            for text_unit_id in (entity.text_unit_ids or [])
        }
        self.path.append("entity-resolve")
        logger.debug(
            "global search: %d community(s), %d matched entity(s)",
            len(self.communities),
            len(entities),
        )

        text_units = artifacts.text_units.set_index("id")["document_id"]
        chunk_ids = sorted(
            {
                int(text_units[tid])
                for tid in text_unit_ids
                if tid in text_units.index and text_units[tid] is not None
            }
        )
        self.path.append("text-unit-resolve")
        logger.debug("global search resolved %d chunk id(s), path=%s", len(chunk_ids), self.path)
        return _nodes_from_chunk_ids(self.store, chunk_ids)
