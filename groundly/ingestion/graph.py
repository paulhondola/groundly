"""The graphrag batch builder — ingestion writes stores, never serves queries
(.claude/rules/architecture.md); local/global search live in retrieval/graph.py.

Groundly feeds graphrag pre-chunked text: each stored chunk becomes one input
"document", with chunking size set generously above CHUNK_MAX_TOKENS so graphrag
produces exactly one text unit per document — `document_id == chunk_id` directly,
no sidecar mapping table needed.
"""

import asyncio
import hashlib
import logging
import shutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.metadata import version as _package_version
from pathlib import Path

import pandas as pd
from graphrag.api.index import build_index
from graphrag.callbacks.noop_workflow_callbacks import NoopWorkflowCallbacks
from graphrag.config.models.community_reports_config import CommunityReportsConfig
from graphrag.config.models.extract_graph_config import ExtractGraphConfig
from graphrag.config.models.graph_rag_config import GraphRagConfig
from graphrag.config.models.reporting_config import ReportingConfig
from graphrag.config.models.summarize_descriptions_config import SummarizeDescriptionsConfig
from graphrag_cache import CacheConfig
from graphrag_chunking.chunking_config import ChunkingConfig
from graphrag_llm.config import ModelConfig
from graphrag_storage import StorageConfig
from graphrag_vectors import VectorStoreConfig

from groundly.core.config import load_settings
from groundly.core.manifest import EMBEDDING_DIM, Graphrag
from groundly.core.store import SQLiteSubjectStore, connect_progress, record_trace
from groundly.core.subject import Subject
from groundly.llm.config import require_provider
from groundly.llm.graphrag_adapter import (
    BGE_M3_EMBEDDING_TYPE,
    ExtractionPromptError,
    allow_nonstandard_service_tier,
    completion_model_config,
    extraction_entity_types,
    extraction_fingerprint,
    prompt_budgets,
    register_bge_m3_embedding,
    resolve_extraction_prompt,
)

logger = logging.getLogger(__name__)

# Generously above manifest.CHUNK_MAX_TOKENS (512) so graphrag's own chunker never
# splits a Groundly chunk further — one text unit per document, guaranteed.
# Internal-only: never exposed in the manifest or CLI (not an interchange knob).
_GRAPH_CHUNK_SIZE = 4096
_COMPLETION_MODEL_ID = "default_completion_model"
_EMBEDDING_MODEL_ID = "default_embedding_model"

# Above this share of chunks failing entity extraction, the graph is missing too much
# of the corpus to be presented as the corpus — refuse rather than stamp the manifest.
# Below it, a transient blip shouldn't throw away a long build; the count is reported.
_MAX_EXTRACTION_FAILURE_RATE = 0.05

# graphrag swallows per-item LLM failures in both of these stages and only logs them,
# so these loggers are the only signal that content is being dropped. Each stage logs a
# failure *twice* — the extractor's own `logger.exception` plus the calling operation's
# `on_error` — so attach to the extractor specifically or every count doubles (verified
# on real runs: 252 records from each extract_graph logger, 44 from each community one).
_EXTRACTION_ERROR_SOURCE = "graphrag.index.operations.extract_graph.graph_extractor"
_COMMUNITY_ERROR_SOURCE = (
    "graphrag.index.operations.summarize_communities.community_reports_extractor"
)


# A rebuild inherits nothing but these. `cache/` is graphrag's LLM response cache —
# expensive and already paid for, so a retry must keep it; `logs/` is how a failed run
# gets diagnosed. Everything else under graph/ is derived output.
_PRESERVED_ON_REBUILD = frozenset({"cache", "logs"})


class GraphBuildError(Exception):
    """Wraps any graphrag indexing failure — no raw traceback ever surfaces."""


@dataclass(frozen=True)
class GraphBuildResult:
    """What the build actually managed to index. `failed` is chunks graphrag dropped
    during entity extraction, `reports_failed` communities whose summary it dropped —
    see _WorkflowErrorCounter for why neither can come from build_index."""

    chunks: int
    failed: int
    reports_failed: int = 0


class _WorkflowErrorCounter(logging.Handler):
    """Counts per-item LLM failures for one graphrag stage, which reach us no other way.

    graphrag catches them per item (an `except Exception` -> `logger.exception` ->
    `on_error` callback) and carries on, so they never appear in
    `PipelineRunResult.error` and a build that dropped most of its content still
    reports success. Entity extraction drops chunks; community reports drop the
    summaries that global search and `overview` answer from.

    Attached to the exact extractor logger rather than to `graphrag`: `init_loggers`
    clears handlers on `graphrag`/`graphrag_llm` when build_index starts, but never on
    their descendants. Works with Groundly logging off too, since init_loggers always
    raises that tree to at least INFO.
    """

    def __init__(self, source: str) -> None:
        super().__init__(level=logging.ERROR)
        self.source = source
        self.count = 0
        self.last_message = ""

    def emit(self, record: logging.LogRecord) -> None:
        self.count += 1
        if record.exc_info and record.exc_info[1] is not None:
            self.last_message = str(record.exc_info[1])
        else:
            self.last_message = record.getMessage()

    def __enter__(self) -> "_WorkflowErrorCounter":
        logging.getLogger(self.source).addHandler(self)
        return self

    def __exit__(self, *_exc: object) -> None:
        logging.getLogger(self.source).removeHandler(self)


class _ProgressCallbacks(NoopWorkflowCallbacks):
    """Translates graphrag's workflow lifecycle into `on_event(description,
    completed, total)` — plain types, so the CLI never imports graphrag."""

    def __init__(self, on_event: Callable[[str, int, int], None]) -> None:
        self._on_event = on_event
        self._total = 0
        self._completed = 0

    def pipeline_start(self, names: list[str]) -> None:
        self._total = len(names)
        self._on_event("starting…", self._completed, self._total)

    def workflow_start(self, name: str, instance: object) -> None:
        self._on_event(name, self._completed, self._total)

    def workflow_end(self, name: str, instance: object) -> None:
        self._completed += 1
        self._on_event(name, self._completed, self._total)

    def pipeline_error(self, error: BaseException) -> None:
        logger.error("graphrag pipeline error: %s", error, exc_info=error)


def _probe_extraction(
    subj: Subject, config: GraphRagConfig, sample_text: str, context_window: int
) -> None:
    """Send one real extraction prompt before committing to the whole corpus.

    Entity extraction is the largest prompt graphrag sends per chunk, and its failure
    mode is silent (see _WorkflowErrorCounter), so a model whose context is too
    small burns hours producing an empty graph. One call up front turns that into a
    named error in seconds.

    Two calls, because the build depends on two independent provider capabilities and a
    reachability check proves neither: the extraction prompt itself (size), and JSON mode
    (community reports are the one stage that requests `response_format`). Each is a real
    billable call, so each records its own trace row (.claude/rules/architecture.md:
    every LLM call records tokens + cost).

    The extraction prompt comes from the *same config the build will use*, and is
    formatted with the *same keys* graphrag formats it with — only `entity_types` and
    `input_text` (graph_extractor._process_document). Passing the delimiter keys too, as
    this used to, made the probe more forgiving than the build: a prompt containing
    `{tuple_delimiter}` formatted fine here and then raised KeyError on every chunk of
    the real run. graphrag_adapter rejects such a prompt outright now, and this stays
    aligned so the probe can never be the laxer of the two."""
    from groundly.llm.chat import complete

    prompt = config.extract_graph.resolved_prompts().extraction_prompt.format(
        entity_types=",".join(config.extract_graph.entity_types),
        input_text=sample_text,
    )
    conn = connect_progress(subj.progress_db_path)
    try:
        # Deliberately does not assert *why* this one failed: it catches every provider
        # refusal, and guessing "your context is too small" sent a real user to check
        # their context window over a 413 a ~1800-token prompt could not have caused.
        _probe_call(
            conn,
            lambda: complete("extraction", [{"role": "user", "content": prompt}]),
            f"a ~{len(prompt) // 4}-token probe prompt to the extraction model failed: {{exc}}. "
            "That prompt is graphrag's few-shot preamble plus one chunk — the smallest call "
            "the build makes. If the server reports a context or size limit, raise the model's "
            "context window (for LM Studio, the model's load settings) or lower "
            f"graph.context_window (currently {context_window}). If the prompt looks far too "
            "small for that, check that extraction.model is a plain chat model — agentic or "
            "tool-using endpoints have their own request limits and are the wrong fit here.",
        )
        # A separate capability: DeepSeek's deepseek-v4-flash answers plain completions
        # fine and rejects response_format outright, which used to surface 80 minutes in
        # as KeyError 'community' — pandas merging the empty reports frame every failed
        # community call left behind.
        _probe_call(
            conn,
            lambda: complete(
                "extraction",
                [{"role": "user", "content": 'Reply with the JSON object {"ok": true}'}],
                json_object=True,
            ),
            "the extraction model rejected a JSON-mode (response_format) request: {exc}. "
            "graphrag requires JSON mode for community reports — the summaries global "
            "search and `overview` answer from — so a model without it cannot finish a "
            "graph build. Switch extraction.model to one that supports structured output.",
        )
    finally:
        conn.close()


def _probe_call(conn, call: Callable[[], object], failure_message: str) -> None:
    """Run one probe call, trace it either way, and wrap any failure as a named
    GraphBuildError. Catches `Exception`, not just `ChatUnreachableError`: this runs
    outside build_graph's own wrapper, so anything else escaping here would reach the
    CLI as a raw traceback past its `except (GraphBuildError, ProviderNotConfiguredError)`.
    `failure_message` is a template with a single `{exc}` placeholder."""
    try:
        result = call()
    except Exception as exc:
        record_trace(
            conn, kind="index", query="", outcome="error", arm="graph-probe", error=str(exc)
        )
        logger.debug("extraction probe failed", exc_info=True)
        raise GraphBuildError(failure_message.format(exc=exc)) from exc
    record_trace(
        conn,
        kind="index",
        query="",
        outcome="built",
        arm="graph-probe",
        model=getattr(result, "model", None),
        tokens=getattr(result, "tokens", None),
        cost_usd=getattr(result, "cost_usd", None),
    )


@contextmanager
def _extraction_prompt() -> Iterator[tuple[Path, str]]:
    """`resolve_extraction_prompt` with its named error mapped onto this module's, so the
    CLI's existing `except GraphBuildError` covers a bad custom prompt with no new
    handler — and so the message still names the cause."""
    try:
        with resolve_extraction_prompt() as resolved:
            yield resolved
    except ExtractionPromptError as exc:
        raise GraphBuildError(str(exc)) from exc


def corpus_hash(store: SQLiteSubjectStore) -> str:
    """sha256 over the subject's indexed materials' sha256s, sorted — stable across
    re-runs of the same corpus, changes iff a material is added/removed/re-extracted."""
    sha256s = sorted(row["sha256"] for row in store.list_materials() if row["status"] == "indexed")
    return hashlib.sha256("\n".join(sha256s).encode()).hexdigest()


def current_extraction_fingerprint() -> str:
    """The fingerprint a build started right now would record. Raises
    ExtractionPromptError if a configured custom prompt cannot be used."""
    with resolve_extraction_prompt() as (_path, text):
        return extraction_fingerprint(text, extraction_entity_types())


def graph_is_stale(subj: Subject, store: SQLiteSubjectStore) -> str | None:
    """Why the recorded graph no longer describes this subject, or None if it still does.

    A reason string rather than a bool because there are now three causes and the CLI
    quotes this to the student. Telling someone "the corpus changed" when what they
    changed was `graph.entity_types` is exactly the kind of confident-but-wrong message
    the gates in this module exist to prevent (conventions: name the cause specifically).
    """
    manifest = subj.load_manifest()
    if manifest.graphrag.corpus_hash is None:
        return "no graph has been recorded for this subject"
    if not (subj.root_dir / "graph").exists():
        return "the graph directory is missing"
    if manifest.graphrag.corpus_hash != corpus_hash(store):
        return "the corpus changed since the last build"
    if manifest.graphrag.extraction_fingerprint != current_extraction_fingerprint():
        return "the extraction prompt or entity types changed since the last build"
    return None


def _build_config(
    subj: Subject, context_window: int, prompt_path: Path, entity_types: list[str]
) -> GraphRagConfig:
    """graphrag's config, rooted entirely under <subject>/graph/ — nothing touches
    cwd, nothing leaks outside the subject's own directory. Every prompt budget is
    scaled to `context_window` (graph.context_window in config.toml); graphrag's own
    defaults assume ~16k and 400 out on a small local model.

    `prompt_path`/`entity_types` come from the caller rather than being read here, so
    the fingerprint recorded in the manifest is computed from the same resolution that
    produced this config — they cannot drift apart."""
    graph_dir = subj.root_dir / "graph"
    budgets = prompt_budgets(context_window)
    return GraphRagConfig(
        extract_graph=ExtractGraphConfig(
            max_gleanings=budgets.max_gleanings,
            # A path, not text — graphrag's resolved_prompts() reads it off disk.
            prompt=str(prompt_path),
            entity_types=entity_types,
        ),
        summarize_descriptions=SummarizeDescriptionsConfig(
            max_input_tokens=budgets.summarize_max_input_tokens,
            max_length=budgets.summarize_max_length,
        ),
        community_reports=CommunityReportsConfig(
            max_input_length=budgets.community_max_input_length,
            max_length=budgets.community_max_length,
        ),
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


def _reset_graph_artifacts(subj: Subject) -> None:
    """Drop the previous build's outputs, and the manifest's claim to a graph, before a
    rebuild starts.

    graphrag writes into an existing `graph/` without clearing it, so a build that
    produces nothing leaves the *previous* build's parquet in place — and the gates in
    build_graph, which check that entities.parquet exists and has rows, pass on those and
    stamp a fresh corpus_hash over a stale graph (verified: a second build writing nothing
    and logging no failures inherited build 1's entities and was recorded as current).

    Resetting `manifest.graphrag` in the same step is what makes this safe rather than a
    new crash: `_require_graph` treats a non-None corpus_hash as "there is a graph here",
    so clearing the artifacts while leaving the old hash behind would send the query path
    into `_load_artifacts` looking for parquet that no longer exists. Cleared together,
    a failed rebuild leaves an honest "no graph" that `graph_is_stale` re-prompts on.

    The previous graph is genuinely lost if the rebuild fails — correct, because a rebuild
    only runs when the corpus already changed, so that graph was no longer a graph *of
    this corpus*. Serving it is the staleness lie the gates exist to prevent."""
    graph_dir: Path = subj.root_dir / "graph"
    if graph_dir.exists():
        for entry in graph_dir.iterdir():
            if entry.name in _PRESERVED_ON_REBUILD:
                continue
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    manifest = subj.load_manifest()
    if manifest.graphrag.corpus_hash is not None:
        manifest.graphrag = Graphrag()
        subj.save_manifest(manifest)


def build_graph(
    subj: Subject,
    store: SQLiteSubjectStore,
    *,
    estimated_tokens: int = 0,
    estimated_cost_usd: float | None = None,
    on_event: Callable[[str, int, int], None] | None = None,
) -> GraphBuildResult:
    """Batch build graphrag's graph for a subject (parquet artifacts + a LanceDB
    vector store under <subject>/graph/). Cost-estimate confirmation is the CLI's
    job, not this module's — ingestion never does interactive I/O.

    `estimated_tokens`/`estimated_cost_usd` are the CLI's already-computed
    `graphrag_adapter.estimate_cost()` figures, threaded through so this function
    doesn't recompute them — recorded into progress.db as a trace row on success.

    `on_event` reports workflow-level progress (mirrors ingestion/pipeline.py's
    own `on_event` contract).

    graphrag is deliberately left at its own default INFO level — `build_index`'s
    `verbose=True` would raise its loggers to DEBUG, and `graphrag/api/index.py`
    logs `str(output.result)` at DEBUG, which for the text-unit workflows is a
    sample DataFrame *including the text column*: verbatim course material onto
    stderr and into graph/logs/indexing-engine.log. INFO already carries the
    workflow names and progress counts that `--debug` exists to show."""
    on_event = on_event or (lambda description, completed, total: None)
    provider_cfg = require_provider("extraction")  # fail fast, before any chunk enumeration
    context_window = load_settings().graph.context_window

    rows = store.all_chunks()
    if not rows:
        raise GraphBuildError("nothing indexed yet — run `groundly index` before building a graph")

    # The prompt context spans the whole pipeline: ExtractGraphConfig.prompt is a path
    # graphrag re-reads at extraction time, so the file has to outlive build_index.
    with (
        _extraction_prompt() as (prompt_path, prompt_text),
        _WorkflowErrorCounter(_EXTRACTION_ERROR_SOURCE) as counter,
        _WorkflowErrorCounter(_COMMUNITY_ERROR_SOURCE) as reports_counter,
    ):
        entity_types = extraction_entity_types()
        fingerprint = extraction_fingerprint(prompt_text, entity_types)

        # Outside the try below on purpose: the config embeds the extraction provider's
        # api_key, and a pydantic ValidationError echoes the offending input value — so
        # neither the wrapped message nor a logged traceback may ever carry this one.
        try:
            config = _build_config(subj, context_window, prompt_path, entity_types)
        except Exception as exc:
            raise GraphBuildError(
                "graphrag config is invalid — check [providers.extraction] in your config.toml "
                f"against the pinned graphrag {_package_version('graphrag')} "
                f"({type(exc).__name__}; details withheld, they would include your api_key)"
            ) from exc

        _probe_extraction(subj, config, rows[0]["text"], context_window)

        # After the probe on purpose: a misconfigured provider should fail without
        # destroying a graph that still works, so nothing is cleared until the provider
        # has answered a real extraction prompt and a JSON-mode call.
        _reset_graph_artifacts(subj)

        try:
            register_bge_m3_embedding()
            allow_nonstandard_service_tier()

            df = pd.DataFrame(
                {
                    "id": [str(row["chunk_id"]) for row in rows],
                    "text": [row["text"] for row in rows],
                    "title": [f"{row['filename']}#p{row['page']}" for row in rows],
                    "creation_date": ["" for _ in rows],
                    "raw_data": [None for _ in rows],
                }
            )

            logger.debug("starting graph build: %d chunks, model=%s", len(rows), provider_cfg.model)
            results = asyncio.run(
                build_index(
                    config,
                    input_documents=df,
                    callbacks=[_ProgressCallbacks(on_event)],
                )
            )
        except Exception as exc:
            logger.debug("graph build failed", exc_info=True)
            raise GraphBuildError(f"graph build failed: {exc}") from exc

    failed = [r for r in results if r.error is not None]
    if failed:
        for r in failed:
            logger.debug("workflow %s failed", r.workflow, exc_info=r.error)
        names = ", ".join(r.workflow for r in failed)
        # A workflow error is graphrag's *symptom*; the swallowed per-item failures
        # above are usually the cause. `create_community_reports` raising
        # `KeyError: 'community'` is what an empty reports frame looks like when every
        # report call failed — pandas merging a frame that has no columns.
        cause = reports_counter.last_message or counter.last_message
        detail = f" — last LLM error: {cause}" if cause else ""
        raise GraphBuildError(f"graph build failed: workflow(s) {names} failed{detail}")

    # graphrag swallows per-chunk extraction failures, so nothing above catches a
    # graph that quietly omits most of the corpus. Refuse before the manifest is
    # stamped: an unstamped manifest leaves the graph stale, so the next index retries.
    if counter.count > len(rows) * _MAX_EXTRACTION_FAILURE_RATE:
        raise GraphBuildError(
            f"entity extraction failed for {counter.count} of {len(rows)} chunks — the graph "
            f"would be missing most of the course, so it was not recorded and stays unusable "
            f"until a build succeeds. Last error: {counter.last_message}. If this is a "
            f"context-size error, raise your extraction model's context window or lower "
            f"graph.context_window (currently {context_window})"
        )

    # The artifact retrieval/graph.py actually reads. Row count, not file size: a
    # zero-row parquet is ~1.9 KB of schema, so a size check would pass an empty graph
    # (reachable when a model returns unparseable output that never raises).
    entities = subj.root_dir / "graph" / "entities.parquet"
    if not entities.exists() or len(pd.read_parquet(entities)) == 0:
        raise GraphBuildError(
            "graph build produced no entities — nothing was extracted from the corpus. "
            "Re-run with --debug to see graphrag's own errors"
        )

    # Community reports are what global search and `overview` answer from, and graphrag
    # swallows their failures the same way it swallows extraction's. A graph with
    # communities but no reports for them is a graph the global arm cannot use.
    graph_dir = subj.root_dir / "graph"
    communities = graph_dir / "communities.parquet"
    reports = graph_dir / "community_reports.parquet"
    community_count = len(pd.read_parquet(communities)) if communities.exists() else 0
    report_count = len(pd.read_parquet(reports)) if reports.exists() else 0
    if community_count and not report_count:
        raise GraphBuildError(
            f"none of the {community_count} community summaries could be generated, so "
            f"global search and `overview` would have nothing to answer from. Last error: "
            f"{reports_counter.last_message or 'unknown'}. Community reports are the one "
            f"stage that requires JSON mode — if your provider reports response_format as "
            f"unavailable, switch extraction.model to one that supports structured output"
        )
    if reports_counter.count:
        logger.warning(
            "community reports failed for %d of %d communities: %s",
            reports_counter.count,
            community_count,
            reports_counter.last_message,
        )

    if counter.count:
        logger.warning(
            "entity extraction failed for %d of %d chunks: %s",
            counter.count,
            len(rows),
            counter.last_message,
        )

    manifest = subj.load_manifest()
    manifest.graphrag = Graphrag(
        version=_package_version("graphrag"),
        extraction_model=provider_cfg.model,
        corpus_hash=corpus_hash(store),
        # Same write as corpus_hash, so a refused build records neither and the next
        # `groundly index` re-offers the build.
        extraction_fingerprint=fingerprint,
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

    return GraphBuildResult(
        chunks=len(rows), failed=counter.count, reports_failed=reports_counter.count
    )
