"""The graphrag batch builder — ingestion writes stores, never serves queries
(.claude/rules/architecture.md); local/global search live in retrieval/graph.py.

Groundly feeds graphrag pre-chunked text: each stored chunk becomes one input
"document", with chunking size set generously above CHUNK_MAX_TOKENS so graphrag
produces exactly one text unit per document — `document_id == chunk_id` directly,
no sidecar mapping table needed.
"""

import asyncio
import hashlib
from importlib.metadata import version as _package_version

import pandas as pd
from graphrag.api.index import build_index
from graphrag.config.models.graph_rag_config import GraphRagConfig
from graphrag.config.models.reporting_config import ReportingConfig
from graphrag_cache import CacheConfig
from graphrag_chunking.chunking_config import ChunkingConfig
from graphrag_llm.config import ModelConfig
from graphrag_storage import StorageConfig
from graphrag_vectors import VectorStoreConfig

from groundly.core.manifest import EMBEDDING_DIM, Graphrag
from groundly.core.store import SQLiteSubjectStore, connect_progress, record_trace
from groundly.core.subject import Subject
from groundly.llm.config import require_provider
from groundly.llm.graphrag_adapter import (
    BGE_M3_EMBEDDING_TYPE,
    completion_model_config,
    register_bge_m3_embedding,
)

# Generously above manifest.CHUNK_MAX_TOKENS (512) so graphrag's own chunker never
# splits a Groundly chunk further — one text unit per document, guaranteed.
# Internal-only: never exposed in the manifest or CLI (not an interchange knob).
_GRAPH_CHUNK_SIZE = 4096
_COMPLETION_MODEL_ID = "default_completion_model"
_EMBEDDING_MODEL_ID = "default_embedding_model"


class GraphBuildError(Exception):
    """Wraps any graphrag indexing failure — no raw traceback ever surfaces."""


def corpus_hash(store: SQLiteSubjectStore) -> str:
    """sha256 over the subject's indexed materials' sha256s, sorted — stable across
    re-runs of the same corpus, changes iff a material is added/removed/re-extracted."""
    sha256s = sorted(row["sha256"] for row in store.list_materials() if row["status"] == "indexed")
    return hashlib.sha256("\n".join(sha256s).encode()).hexdigest()


def graph_is_stale(subj: Subject, store: SQLiteSubjectStore) -> bool:
    """True if a graph build was recorded but its directory vanished (deleted
    externally — be defensive), or if the corpus has changed since the last build."""
    manifest = subj.load_manifest()
    if manifest.graphrag.corpus_hash is not None and not (subj.root_dir / "graph").exists():
        return True
    return manifest.graphrag.corpus_hash != corpus_hash(store)


def _build_config(subj: Subject) -> GraphRagConfig:
    """graphrag's config, rooted entirely under <subject>/graph/ — nothing touches
    cwd, nothing leaks outside the subject's own directory."""
    graph_dir = subj.root_dir / "graph"
    return GraphRagConfig(
        completion_models={_COMPLETION_MODEL_ID: completion_model_config()},
        embedding_models={
            _EMBEDDING_MODEL_ID: ModelConfig(
                type=BGE_M3_EMBEDDING_TYPE,
                model_provider=BGE_M3_EMBEDDING_TYPE,
                model="bge-m3",
            )
        },
        chunking=ChunkingConfig(size=_GRAPH_CHUNK_SIZE, overlap=0),
        # input is unused (input_documents bypasses graphrag's own file loading
        # entirely) but the config is still validated — root it under graph/ so
        # nothing gets created relative to cwd.
        input_storage=StorageConfig(base_dir=str(graph_dir / "input")),
        output_storage=StorageConfig(base_dir=str(graph_dir)),
        update_output_storage=StorageConfig(base_dir=str(graph_dir / "update_output")),
        reporting=ReportingConfig(base_dir=str(graph_dir / "logs")),
        cache=CacheConfig(storage=StorageConfig(base_dir=str(graph_dir / "cache"))),
        vector_store=VectorStoreConfig(
            db_uri=str(graph_dir / "lancedb"), vector_size=EMBEDDING_DIM
        ),
    )


def build_graph(
    subj: Subject,
    store: SQLiteSubjectStore,
    *,
    estimated_tokens: int = 0,
    estimated_cost_usd: float | None = None,
) -> None:
    """Batch build graphrag's graph for a subject (parquet artifacts + a LanceDB
    vector store under <subject>/graph/). Cost-estimate confirmation is the CLI's
    job, not this module's — ingestion never does interactive I/O.

    `estimated_tokens`/`estimated_cost_usd` are the CLI's already-computed
    `graphrag_adapter.estimate_cost()` figures, threaded through so this function
    doesn't recompute them — recorded into progress.db as a trace row on success."""
    provider_cfg = require_provider("extraction")  # fail fast, before any chunk enumeration

    try:
        register_bge_m3_embedding()

        rows = store.all_chunks()
        df = pd.DataFrame(
            {
                "id": [str(row["chunk_id"]) for row in rows],
                "text": [row["text"] for row in rows],
                "title": [f"{row['filename']}#p{row['page']}" for row in rows],
                "creation_date": ["" for _ in rows],
                "raw_data": [None for _ in rows],
            }
        )

        config = _build_config(subj)
        asyncio.run(build_index(config, input_documents=df))
    except Exception as exc:
        raise GraphBuildError(f"graph build failed: {exc}") from exc

    manifest = subj.load_manifest()
    manifest.graphrag = Graphrag(
        version=_package_version("graphrag"),
        extraction_model=provider_cfg.model,
        corpus_hash=corpus_hash(store),
    )
    subj.save_manifest(manifest)

    # This is the pre-build heuristic estimate (chars // 4), not metered actual usage —
    # graphrag's own internal extraction LLM calls aren't instrumented through llm/, so
    # exact tokens/cost are unknowable here (see retrieval/graph.py's module docstring
    # for the query-side equivalent gap).
    conn = connect_progress(subj.progress_db_path)
    try:
        record_trace(
            conn,
            kind="index",
            query="",
            outcome="built",
            arm="graph-build",
            model=provider_cfg.model,
            tokens=estimated_tokens,
            cost_usd=estimated_cost_usd,
        )
    finally:
        conn.close()
