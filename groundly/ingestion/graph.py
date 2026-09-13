"""The graphrag batch builder. Ingestion writes stores; it never serves queries.

Each stored chunk goes to graphrag as one input document, with graphrag's chunk size far
above CHUNK_MAX_TOKENS, so `document_id == chunk_id` and no mapping table is needed.
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
from graphrag.index.operations.summarize_communities.community_reports_extractor import (
    CommunityReportResponse,
)
from graphrag_cache import CacheConfig
from graphrag_chunking.chunking_config import ChunkingConfig
from graphrag_storage import StorageConfig

from groundly.core.config import load_settings
from groundly.core.manifest import Graphrag
from groundly.core.progress import connect_progress, record_trace
from groundly.core.store import SubjectStore
from groundly.core.subject import Subject
from groundly.llm.config import ProviderConfig, require_provider
from groundly.llm.graph_cost import metered_usage, reset_metered_usage
from groundly.llm.graphrag_adapter import (
    COMPLETION_MODEL_ID,
    ExtractionPromptError,
    allow_nonstandard_service_tier,
    bge_m3_embedding_models,
    completion_model_config,
    concurrent_requests,
    extraction_entity_types,
    extraction_fingerprint,
    graph_vector_store,
    prompt_budgets,
    register_bge_m3_embedding,
    register_groundly_metrics_store,
    resolve_extraction_prompt,
)

logger = logging.getLogger(__name__)

# Well above manifest.CHUNK_MAX_TOKENS (512), so graphrag never splits a Groundly chunk.
# Internal only: not an interchange knob.
_GRAPH_CHUNK_SIZE = 4096

# Above this share of chunks failing entity extraction, the graph is missing too much
# of the corpus to be presented as the corpus — refuse rather than stamp the manifest.
# Below it, a transient blip shouldn't throw away a long build; the count is reported.
_MAX_EXTRACTION_FAILURE_RATE = 0.05

# graphrag swallows per-item LLM failures in these stages and only logs them. Each failure
# is logged twice (the extractor's `logger.exception`, then the operation's `on_error`), so
# attach to the extractor's logger or every count doubles.
_EXTRACTION_ERROR_SOURCE = "graphrag.index.operations.extract_graph.graph_extractor"
_COMMUNITY_ERROR_SOURCE = (
    "graphrag.index.operations.summarize_communities.community_reports_extractor"
)

# The preflight probe's progress phase: it runs before graphrag reports anything, and
# makes two calls (see _probe_extraction).
_PROBE_STEP = "checking the extraction model…"
_PROBE_CALLS = 2


# A rebuild keeps only these: `cache/` holds graphrag's already-paid LLM responses, and
# `logs/` is how a failed run gets diagnosed. Everything else under graph/ is derived.
_PRESERVED_ON_REBUILD = frozenset({"cache", "logs"})


class GraphBuildError(Exception):
    """Wraps any graphrag indexing failure — no raw traceback ever surfaces."""


@dataclass(frozen=True)
class GraphBuildResult:
    """What the build indexed. `failed` counts chunks graphrag dropped during extraction,
    `reports_failed` communities whose summary it dropped (see _WorkflowErrorCounter)."""

    chunks: int
    failed: int
    reports_failed: int = 0
    # Metered from graphrag's own aggregates; None when nothing was metered. `cost_usd`
    # also needs prices, so it can be None while the counts are not.
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None


@dataclass(frozen=True)
class _BuildPlan:
    """Every input one graph build depends on, resolved exactly once.

    The probe, the model config and the manifest all read from this, so a `config set`
    landing mid-build cannot make them disagree.
    """

    provider: ProviderConfig
    context_window: int
    gleanings: int
    entity_types: list[str]
    prompt_path: Path
    prompt_text: str

    @property
    def fingerprint(self) -> str:
        """What the manifest records, derived from the same read the build ran on."""
        return extraction_fingerprint(self.prompt_text, self.entity_types, self.gleanings)


@contextmanager
def _plan_build() -> Iterator[_BuildPlan]:
    """Resolve the plan, keeping the extraction prompt file available while it lives.

    graphrag re-reads `ExtractGraphConfig.prompt` from disk at extraction time, so the file
    must outlive `build_index`. The provider resolves first, so a misconfigured one fails
    before any corpus read or graph reset."""
    provider = require_provider("extraction")
    settings = load_settings()
    with _extraction_prompt() as (prompt_path, prompt_text):
        yield _BuildPlan(
            provider=provider,
            context_window=settings.graph.context_window,
            gleanings=settings.graph.gleanings,
            entity_types=extraction_entity_types(),
            prompt_path=prompt_path,
            prompt_text=prompt_text,
        )


class _WorkflowErrorCounter(logging.Handler):
    """Counts per-item LLM failures for one graphrag stage, which reach us no other way.

    graphrag catches them per item and carries on, so they never reach
    `PipelineRunResult.error` and a build that dropped most of its content reports success.
    Attached to the exact extractor logger, not `graphrag`: `init_loggers` clears handlers
    on `graphrag`/`graphrag_llm` when build_index starts, but not on their descendants.
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
    subj: Subject,
    config: GraphRagConfig,
    sample_text: str,
    plan: _BuildPlan,
    on_event: Callable[[str, int, int], None],
) -> None:
    """Check the provider with what the build sends before committing to the whole corpus.

    Extraction fails silently per chunk (see _WorkflowErrorCounter), so a model that cannot
    take the prompt would burn hours on an empty graph. One call per provider capability
    the build needs: the extraction prompt, and structured output (community reports send
    `response_format`). **Both send the build's own objects**, so the probe is never laxer
    or stricter than the build: the prompt from the same config, formatted with only the
    keys graphrag uses, and graphrag's own `CommunityReportResponse` rather than a
    hand-written json_object. Each call is billable, so each records a trace row."""
    from groundly.llm.chat import complete, loaded_context_length

    prompt = config.extract_graph.resolved_prompts().extraction_prompt.format(
        entity_types=",".join(config.extract_graph.entity_types),
        input_text=sample_text,
    )
    on_event(_PROBE_STEP, 0, _PROBE_CALLS)
    conn = connect_progress(subj.progress_db_path)
    try:
        # Deliberately does not guess *why* this one failed: it catches every provider
        # refusal, and a 413 or a bad key is not a context-window problem.
        _probe_call(
            conn,
            lambda: complete("extraction", [{"role": "user", "content": prompt}]),
            f"a ~{len(prompt) // 4}-token probe prompt to the extraction model failed: {{exc}}. "
            "That prompt is graphrag's few-shot preamble plus one chunk — the smallest call "
            "the build makes. If the server reports a context or size limit, raise the model's "
            "context window (for LM Studio, the model's load settings) or lower "
            f"graph.context_window (currently {plan.context_window}). If the prompt looks far too "
            "small for that, check that extraction.model is a plain chat model — agentic or "
            "tool-using endpoints have their own request limits and are the wrong fit here.",
        )
        on_event(_PROBE_STEP, 1, _PROBE_CALLS)
        # Only after the first call: a local runtime JIT-loads on first request. Warn, never
        # refuse, since only LM Studio publishes the field; a smaller served window breaks
        # every prompt budget at once, and the build would fail late, in community reports.
        loaded = loaded_context_length("extraction")
        if loaded is not None and loaded < plan.context_window:
            logger.warning(
                "extraction model is loaded with a %d-token context but graph.context_window "
                "is %d — every prompt budget in this build is sized for %d. Raise the model's "
                "load setting to %d, or lower graph.context_window to %d",
                loaded,
                plan.context_window,
                plan.context_window,
                plan.context_window,
                loaded,
            )
        # A provider can serve plain completions and still reject structured output, which
        # otherwise surfaces deep in the build as KeyError 'community'. Passing graphrag's own
        # response model makes litellm derive the same wire request the build sends.
        _probe_call(
            conn,
            lambda: complete(
                "extraction",
                [{"role": "user", "content": "Summarise a one-entity community."}],
                response_format=CommunityReportResponse,
            ),
            "the extraction model rejected graphrag's structured-output request: {exc}. "
            "Community reports are sent as `response_format: json_schema`, and a model "
            "that refuses it cannot finish a graph build. Note this is a *stricter* "
            'capability than JSON mode: providers that accept `{{"type": "json_object"}}` '
            "may still refuse `json_schema` (every DeepSeek model does). Switch "
            "extraction.model to one whose endpoint supports JSON-schema structured output.",
        )
        on_event(_PROBE_STEP, _PROBE_CALLS, _PROBE_CALLS)
    finally:
        conn.close()


def _probe_call(conn, call: Callable[[], object], failure_message: str) -> None:
    """Run one probe call, trace it either way, and wrap any failure as GraphBuildError.

    Catches every Exception: this runs outside build_graph's wrapper, so anything else would
    reach the CLI as a raw traceback. `failure_message` has a single `{exc}` placeholder."""
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
    """`resolve_extraction_prompt`, its named error re-raised as GraphBuildError so the
    CLI's existing handler covers a bad custom prompt."""
    try:
        with resolve_extraction_prompt() as resolved:
            yield resolved
    except ExtractionPromptError as exc:
        raise GraphBuildError(str(exc)) from exc


def corpus_hash(store: SubjectStore) -> str:
    """sha256 over the subject's indexed materials' sha256s, sorted — stable across
    re-runs of the same corpus, changes iff a material is added/removed/re-extracted."""
    sha256s = sorted(row["sha256"] for row in store.list_materials() if row["status"] == "indexed")
    return hashlib.sha256("\n".join(sha256s).encode()).hexdigest()


def current_extraction_fingerprint() -> str:
    """The fingerprint a build started right now would record. Raises
    ExtractionPromptError if a configured custom prompt cannot be used."""
    with resolve_extraction_prompt() as (_path, text):
        return extraction_fingerprint(
            text, extraction_entity_types(), load_settings().graph.gleanings
        )


def graph_is_stale(subj: Subject, store: SubjectStore) -> str | None:
    """Why the recorded graph no longer describes this subject, or None if it still does.

    A reason string, not a bool: the CLI quotes it, and it must name the actual cause.
    """
    manifest = subj.load_manifest()
    if manifest.graphrag.corpus_hash is None:
        return "no graph has been recorded for this subject"
    if not (subj.root_dir / "graph").exists():
        return "the graph directory is missing"
    if manifest.graphrag.corpus_hash != corpus_hash(store):
        return "the corpus changed since the last build"
    if manifest.graphrag.extraction_fingerprint != current_extraction_fingerprint():
        return "the extraction prompt, entity types or gleaning rounds changed since the last build"
    return None


def _build_config(subj: Subject, plan: _BuildPlan) -> GraphRagConfig:
    """graphrag's config, rooted entirely under <subject>/graph/ so nothing touches cwd.
    Prompt budgets scale to `plan.context_window`. Everything comes off the plan, so the
    recorded fingerprint matches this config."""
    graph_dir = subj.root_dir / "graph"
    budgets = prompt_budgets(plan.context_window, plan.gleanings)

    completion_models = {COMPLETION_MODEL_ID: completion_model_config(track_usage=True)}
    community_reports_kwargs: dict[str, int | str] = {
        "max_input_length": budgets.community_max_input_length,
        "max_length": budgets.community_max_length,
    }

    return GraphRagConfig(
        # graphrag's default of 25 exhausts a local runtime's shared KV cache; see
        # llm/graphrag_adapter.concurrent_requests.
        concurrent_requests=concurrent_requests(plan.provider),
        extract_graph=ExtractGraphConfig(
            max_gleanings=budgets.max_gleanings,
            # A path, not text — graphrag's resolved_prompts() reads it off disk.
            prompt=str(plan.prompt_path),
            entity_types=plan.entity_types,
        ),
        summarize_descriptions=SummarizeDescriptionsConfig(
            max_input_tokens=budgets.summarize_max_input_tokens,
            max_length=budgets.summarize_max_length,
        ),
        community_reports=CommunityReportsConfig(**community_reports_kwargs),
        completion_models=completion_models,
        embedding_models=bge_m3_embedding_models(),
        chunking=ChunkingConfig(size=_GRAPH_CHUNK_SIZE, overlap=0),
        # Unused (input_documents bypasses graphrag's file loading) but still validated,
        # so root it under graph/ too.
        input_storage=StorageConfig(base_dir=str(graph_dir / "input")),
        output_storage=StorageConfig(base_dir=str(graph_dir)),
        update_output_storage=StorageConfig(base_dir=str(graph_dir / "update_output")),
        reporting=ReportingConfig(base_dir=str(graph_dir / "logs")),
        cache=CacheConfig(storage=StorageConfig(base_dir=str(graph_dir / "cache"))),
        vector_store=graph_vector_store(graph_dir),
    )


def _reset_graph_artifacts(subj: Subject) -> None:
    """Before a rebuild, drop the previous outputs and the manifest's claim to a graph.

    graphrag writes into an existing `graph/` without clearing it, so a build that produces
    nothing would leave the old parquet to pass the output gates. `manifest.graphrag` is
    reset in the same step because a recorded corpus_hash is what every reader takes as
    "there is a graph". A failed rebuild therefore loses the old graph, which no longer
    described this corpus anyway."""
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


# Substrings a runtime uses when it is out of *room*, not out of spec. llama.cpp/LM
# Studio answer "Context size has been exceeded"; litellm's own overflow class stringifies
# as "context window exceeded"; the OpenAI surface says "maximum context length".
_CAPACITY_MARKERS = ("context size", "context window", "context length", "too many tokens")


def _report_failure_hint(last_message: str) -> str:
    """A sentence blaming concurrency when the report stage died of context capacity, else "".

    Community reports carry the build's largest prompt, so they are the first to overrun a
    runtime whose real limit is in-flight calls x prompt. Text matching is approximate, but
    it only adds a sentence: a miss costs nothing and a false positive one line."""
    lowered = last_message.lower()
    if not any(marker in lowered for marker in _CAPACITY_MARKERS):
        return ""
    return (
        " That reads as a capacity limit rather than a rejected request: community reports "
        "carry the build's largest prompt, and a local runtime serves several at once from "
        "one shared KV cache, so what has to fit is in-flight calls x prompt. Reduce the "
        "server's parallel slots to 1 (LM Studio: the model's load settings) before "
        "touching graph.context_window."
    )


def _verify_build_output(
    subj: Subject,
    results,
    counter: _WorkflowErrorCounter,
    reports_counter: _WorkflowErrorCounter,
    chunk_count: int,
    plan: _BuildPlan,
) -> None:
    """Refuse a build that reported success but produced an unusable graph. graphrag
    swallows per-item failures, so nothing upstream catches one."""
    failed = [r for r in results if r.error is not None]
    if failed:
        for r in failed:
            logger.debug("workflow %s failed", r.workflow, exc_info=r.error)
        names = ", ".join(r.workflow for r in failed)
        # A workflow error is usually a symptom of swallowed per-item failures: every report
        # call failing leaves an empty frame that pandas merges into `KeyError: 'community'`.
        cause = reports_counter.last_message or counter.last_message
        detail = f" — last LLM error: {cause}" if cause else ""
        raise GraphBuildError(f"graph build failed: workflow(s) {names} failed{detail}")

    # Refuse before the manifest is stamped: an unstamped graph stays stale, so the next
    # index retries.
    if counter.count > chunk_count * _MAX_EXTRACTION_FAILURE_RATE:
        raise GraphBuildError(
            f"entity extraction failed for {counter.count} of {chunk_count} chunks — the graph "
            f"would be missing most of the course, so it was not recorded and stays unusable "
            f"until a build succeeds. Last error: {counter.last_message}. If this is a "
            f"context-size error, raise your extraction model's context window or lower "
            f"graph.context_window (currently {plan.context_window})"
        )

    # Row count, not file size: a zero-row parquet is ~1.9 KB of schema. Reachable when a
    # model returns unparseable output that never raises.
    entities = subj.root_dir / "graph" / "entities.parquet"
    if not entities.exists() or len(pd.read_parquet(entities)) == 0:
        raise GraphBuildError(
            "graph build produced no entities — nothing was extracted from the corpus. "
            "Re-run with --debug to see graphrag's own errors"
        )

    # Community-report failures are swallowed too. Without reports, the export-graph
    # sidebar (and later the topic map) has no community summaries to show.
    graph_dir = subj.root_dir / "graph"
    communities = graph_dir / "communities.parquet"
    reports = graph_dir / "community_reports.parquet"
    community_count = len(pd.read_parquet(communities)) if communities.exists() else 0
    report_count = len(pd.read_parquet(reports)) if reports.exists() else 0
    if community_count and not report_count:
        raise GraphBuildError(
            f"none of the {community_count} community summaries could be generated, so "
            f"the topic map would have nothing to build from. Last error: "
            f"{reports_counter.last_message or 'unknown'}."
            + _report_failure_hint(reports_counter.last_message)
            + " Community reports are the one stage that requires JSON mode — if your "
            "provider reports response_format as unavailable, switch extraction.model to "
            "one that supports structured output"
        )
    if reports_counter.count:
        # A partial failure still loses those communities' summaries, and it is the shape
        # capacity exhaustion usually takes.
        logger.warning(
            "community reports failed for %d of %d communities: %s%s",
            reports_counter.count,
            community_count,
            reports_counter.last_message,
            _report_failure_hint(reports_counter.last_message),
        )

    if counter.count:
        logger.warning(
            "entity extraction failed for %d of %d chunks: %s",
            counter.count,
            chunk_count,
            counter.last_message,
        )


def build_graph(
    subj: Subject,
    store: SubjectStore,
    *,
    estimated_tokens: int = 0,
    estimated_cost_usd: float | None = None,
    on_event: Callable[[str, int, int], None] | None = None,
) -> GraphBuildResult:
    """Build graphrag's graph for a subject: parquet artifacts plus a LanceDB vector store
    under <subject>/graph/. Confirming the cost estimate is the CLI's job; ingestion does no
    interactive I/O. `estimated_*` are the CLI's `estimate_cost()` figures, traced only when
    nothing was metered; `on_event` reports workflow-level progress.

    graphrag stays at its default INFO level: `verbose=True` would log a sample DataFrame
    including the text column at DEBUG, putting verbatim course material on stderr and in
    graph/logs/."""
    on_event = on_event or (lambda description, completed, total: None)

    with (
        _plan_build() as plan,
        _WorkflowErrorCounter(_EXTRACTION_ERROR_SOURCE) as counter,
        _WorkflowErrorCounter(_COMMUNITY_ERROR_SOURCE) as reports_counter,
    ):
        rows = store.all_chunks()
        if not rows:
            raise GraphBuildError(
                "nothing indexed yet — run `groundly index` before building a graph"
            )

        config = _make_config(subj, plan)
        _probe_extraction(subj, config, rows[0]["text"], plan, on_event)

        # Only after the probe: a misconfigured provider must not destroy a graph that works.
        _reset_graph_artifacts(subj)
        results = _run_index(config, rows, plan, on_event)

    _verify_build_output(subj, results, counter, reports_counter, len(rows), plan)
    return _record_build(
        subj,
        store,
        plan,
        chunks=len(rows),
        failed=counter.count,
        reports_failed=reports_counter.count,
        estimated_tokens=estimated_tokens,
        estimated_cost_usd=estimated_cost_usd,
    )


def _make_config(subj: Subject, plan: _BuildPlan) -> GraphRagConfig:
    """`_build_config`, with a failure that never echoes the config.

    The config carries the provider's api_key and a pydantic ValidationError echoes input
    values, so neither the message nor a logged traceback may include it: no
    `logger.debug(exc_info=True)` here."""
    try:
        return _build_config(subj, plan)
    except Exception as exc:
        raise GraphBuildError(
            "graphrag config is invalid — check [providers.extraction] in your config.toml "
            f"against the pinned graphrag {_package_version('graphrag')} "
            f"({type(exc).__name__}; details withheld, they would include your api_key)"
        ) from exc


def _run_index(
    config: GraphRagConfig,
    rows: list,
    plan: _BuildPlan,
    on_event: Callable[[str, int, int], None],
):
    """Run graphrag's pipeline with one input document per stored chunk, `id =
    str(chunk_id)`, so graphrag's `document_id` is Groundly's chunk_id."""
    try:
        register_bge_m3_embedding()
        register_groundly_metrics_store()
        reset_metered_usage()
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

        logger.debug("starting graph build: %d chunks, model=%s", len(rows), plan.provider.model)
        return asyncio.run(
            build_index(config, input_documents=df, callbacks=[_ProgressCallbacks(on_event)])
        )
    except Exception as exc:
        logger.debug("graph build failed", exc_info=True)
        raise GraphBuildError(f"graph build failed: {exc}") from exc


def _record_build(
    subj: Subject,
    store: SubjectStore,
    plan: _BuildPlan,
    *,
    chunks: int,
    failed: int,
    reports_failed: int,
    estimated_tokens: int,
    estimated_cost_usd: float | None,
) -> GraphBuildResult:
    """Stamp the manifest and record what the build spent. Runs only after
    `_verify_build_output` passes, so a refused build records neither the corpus hash nor
    the fingerprint and the next `groundly index` re-offers it."""
    manifest = subj.load_manifest()
    manifest.graphrag = Graphrag(
        version=_package_version("graphrag"),
        extraction_model=plan.provider.model,
        corpus_hash=corpus_hash(store),
        extraction_fingerprint=plan.fingerprint,
    )
    subj.save_manifest(manifest)

    # Metered where possible, estimated only as a fallback: graphrag's calls bypass llm/,
    # but `track_usage=True` exposes graphrag_llm's own usage aggregate to this process.
    metered = metered_usage()
    conn = connect_progress(subj.progress_db_path)
    try:
        record_trace(
            conn,
            kind="index",
            query="",
            outcome="built",
            arm="graph-build",
            model=plan.provider.model,
            tokens=estimated_tokens if metered is None else metered.total_tokens,
            cost_usd=estimated_cost_usd if metered is None else metered.cost_usd,
        )
    finally:
        conn.close()

    return GraphBuildResult(
        chunks=chunks,
        failed=failed,
        reports_failed=reports_failed,
        prompt_tokens=None if metered is None else metered.prompt_tokens,
        completion_tokens=None if metered is None else metered.completion_tokens,
        cost_usd=None if metered is None else metered.cost_usd,
    )
