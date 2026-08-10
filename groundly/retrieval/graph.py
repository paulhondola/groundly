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

Known gap: the synthesis LLM call graphrag makes internally inside `global_search` is
NOT traced/metered by Groundly — graphrag's own LiteLLM client doesn't report usage back
through our `llm/` layer, so this call is invisible to the traces table (a
framework-boundary limitation, not something this module fixes). Local search no longer
has this gap: it never reaches a provider at all (see `_AbortAfterContext`).
"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from graphrag.api.query import global_search, local_search
from graphrag.callbacks.query_callbacks import QueryCallbacks
from graphrag.config.models.graph_rag_config import GraphRagConfig
from graphrag.query.indexer_adapters import read_indexer_entities
from graphrag_llm.config import ModelConfig
from llama_index.core.callbacks import CallbackManager
from llama_index.core.retrievers import BaseRetriever
from llama_index.core.schema import NodeWithScore, QueryBundle

from groundly.core.store import SubjectStore
from groundly.core.subject import Subject
from groundly.llm.config import require_provider
from groundly.llm.graphrag_adapter import (
    COMPLETION_MODEL_ID,
    allow_nonstandard_service_tier,
    bge_m3_embedding_models,
    completion_model_config,
    concurrent_requests,
    graph_vector_store,
    register_bge_m3_embedding,
)
from groundly.retrieval.nodes import node_from_row

logger = logging.getLogger(__name__)

# Matches graphrag CLI's own default (`--community-level`, graphrag/cli/main.py). This
# is a level *ceiling*, not an exact match (`df[df.level <= community_level]` —
# confirmed via graphrag's `_filter_under_community_level`): build and query disagreeing
# here is harmless, a smaller value just means less breadth, never empty/broken results.
COMMUNITY_LEVEL = 2
RESPONSE_TYPE = "multiple paragraphs"
# Unlike COMMUNITY_LEVEL above, query and build MUST agree on the model ids and the vector
# store — they're the keys graphrag looks completion_models/embedding_models up by, and a
# mismatch fails the lookup. That agreement is structural now: the ids, the embedding
# entry and the store live in llm/graphrag_adapter.py and both paths import them, rather
# than each declaring its own copy of the same literals.


class GraphNotBuiltError(Exception):
    """Raised by graph arms whenever `<subject>/graph/` doesn't exist yet."""

    def __init__(self, message: str = "run `groundly index --graph` first") -> None:
        super().__init__(f"graph not built for this subject — {message}")


class _ContextBuilt(Exception):  # noqa: N818 — control flow, not a failure
    """Carries local search's context out of graphrag before the synthesis runs."""

    def __init__(self, context_records) -> None:
        super().__init__("local search context built")
        self.context_records = context_records


class _AbortAfterContext(QueryCallbacks):
    """Stops `local_search` at the point its answer stops being needed.

    `LocalSearch.stream_search` builds the entire context, hands it to `on_context`, and
    only *then* streams a prose answer. Groundly discards that answer — `ask()` writes
    its own from the same chunks, through `llm/` where it is traced — so raising here
    skips a call whose output nothing reads. The chunk ids provably cannot depend on it:
    they are read off `context_records`, which is already complete when this fires.

    Measured on apd, same process, same queries, real provider: 152-208 s with the
    synthesis against 3.6-6.2 s without it (33-49x), and **identical chunk ids in
    identical order** on every question tested. On an idle machine the remaining graph
    half is 0.21-0.45 s. The synthesis was also the only reason this arm needed a
    provider, so aborting makes it zero-key — what `.claude/rules/architecture.md` asks
    of the retrieval path.

    Ordering note: graphrag appends its own `on_context` callback *after* any we pass, so
    this raises before graphrag records the context itself — hence the records travel out
    on the exception rather than through graphrag's return value.
    """

    def on_context(self, context) -> None:
        raise _ContextBuilt(context)


@dataclass
class _GraphArtifacts:
    """A subject's graphrag build artifacts, loaded once per retriever instance."""

    config: GraphRagConfig
    entities: pd.DataFrame
    communities: pd.DataFrame
    community_reports: pd.DataFrame
    text_units: pd.DataFrame
    relationships: pd.DataFrame


def _load_artifacts(graph_dir: Path, *, synthesises: bool = True) -> _GraphArtifacts:
    """Read the parquet artifacts graphrag's build wrote, plus a query-time config.

    `synthesises=False` is local search, which aborts at `on_context` and so never
    reaches a completion model (`_AbortAfterContext`). graphrag still validates a
    `completion_models` entry, so an unreachable placeholder goes in — that is what
    keeps the arm runnable with no `[providers.extraction]` section configured at all,
    rather than merely not *calling* one. Global search still synthesises for real
    (its map-reduce runs before `on_context` fires), so it keeps requiring a provider
    and fails loudly when none is set.
    """
    # Here rather than in either retriever: this is the one place that builds the
    # completion config, so both arms are covered and a future third one can't miss it.
    # Global search was previously left out, so a provider returning a non-OpenAI
    # service_tier still failed every synthesis call on that arm.
    allow_nonstandard_service_tier()
    completion = (
        completion_model_config()
        if synthesises
        else ModelConfig(model_provider="openai", model="unused", api_key="unused")
    )
    config = GraphRagConfig(
        completion_models={COMPLETION_MODEL_ID: completion},
        # graphrag defaults this to 25. Only global search's map phase ever spends it,
        # and 25 concurrent ~12k-token calls is what exhausts a local runtime's shared
        # KV cache — the failure `_map_response_single_batch` swallows into
        # {"answer": "", "score": 0}. The build path has always got this right
        # (ingestion/graph.py); the query path never inherited it.
        # 1 in the non-synthesising case is not a tuning choice — local search issues no
        # completion at all, so the value is unreachable either way.
        concurrent_requests=(
            concurrent_requests(require_provider("extraction")) if synthesises else 1
        ),
        embedding_models=bge_m3_embedding_models(),
        vector_store=graph_vector_store(graph_dir),
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
        nodes.append(node_from_row(row, 1.0 / (rank + 1)))
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

    # Whether this arm ever reaches a completion model — see `_load_artifacts`.
    synthesises = True

    @property
    def artifacts(self) -> _GraphArtifacts:
        if self._artifacts is None:
            self._artifacts = _load_artifacts(self.graph_dir, synthesises=self.synthesises)
        return self._artifacts


class GraphLocalRetriever(_GraphRetrieverBase):
    """Entity-anchored local search — multi-hop queries. Zero-key: the synthesis that
    used to make this arm the most expensive one is aborted at `on_context`."""

    synthesises = False

    def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        self._require_graph()
        register_bge_m3_embedding()  # local search embeds the query to find entities
        artifacts = self.artifacts  # _load_artifacts widens service_tier for both arms

        try:
            asyncio.run(
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
                    callbacks=[_AbortAfterContext()],
                )
            )
        except _ContextBuilt as built:
            context_data = built.context_records
        else:
            # Reaching here means graphrag ran a whole synthesis without ever firing
            # `on_context` — the contract `_AbortAfterContext` depends on is gone,
            # presumably after an upgrade. Returning [] instead would report an empty
            # retrieval as a real result, which is the one failure mode this project
            # treats as worse than crashing.
            raise RuntimeError(
                "graphrag's local search completed without emitting context — "
                "retrieval/graph.py's on_context contract broke, likely on upgrade"
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

        # A LIST of document_ids per text-unit id, not set_index()["document_id"]: text
        # unit ids are content hashes, so two Groundly chunks with byte-identical text
        # collide onto one id while remaining *different chunks* on different pages of
        # different files (apd: 18 of 1193 ids collide — "REVIEW" heads two quiz decks,
        # one QuickSort listing appears in two files). Under an index lookup those rows
        # return a Series instead of a scalar and `int()` raises TypeError, which took
        # global search down entirely. core/graph_html.py's `_entity_citations` resolves
        # the same join the same way, for the same reason.
        doc_ids_by_tu: dict[str, list] = {}
        for tu_id, doc_id in zip(
            artifacts.text_units["id"], artifacts.text_units["document_id"], strict=True
        ):
            doc_ids_by_tu.setdefault(tu_id, []).append(doc_id)

        # The cast is guarded for the same reason graph_html.py guards it: `document_id`
        # is only numeric because ingestion/graph.py sets it to str(chunk_id), and an
        # unguarded int() turns any future deviation into a dead global-search arm rather
        # than a citation quietly skipped.
        resolved: set[int] = set()
        for tid in text_unit_ids:
            for doc_id in doc_ids_by_tu.get(tid, ()):
                if doc_id is None:
                    continue
                try:
                    resolved.add(int(doc_id))
                except (TypeError, ValueError):
                    logger.debug("skipping non-numeric document_id %r", doc_id)
        chunk_ids = sorted(resolved)
        self.path.append("text-unit-resolve")
        logger.debug("global search resolved %d chunk id(s), path=%s", len(chunk_ids), self.path)
        return _nodes_from_chunk_ids(self.store, chunk_ids)
