# Debug logging for Groundly, starting with the graphrag build

## Context

`groundly index --graph` goes silent for minutes. [cli/subjects.py:165](groundly/cli/subjects.py:165) calls `build_graph` between two `console.print` lines with nothing in between — no progress, no indication of which of graphrag's ~12 workflows is running, no way to see why a build failed beyond a one-line wrapped message.

Three findings from the exploration shape the design:

1. **graphrag already logs.** [ingestion/graph.py:82](groundly/ingestion/graph.py:82) sets `reporting=ReportingConfig(base_dir=graph/logs)`, and `build_index` calls `init_loggers(config, verbose=verbose)`, which attaches a `FileHandler` writing INFO to `~/.groundly/<SUBJECT>/graph/logs/indexing-engine.log`. That file exists today, unseen (and correctly excluded from bundle export at [core/bundle.py:60-63](groundly/core/bundle.py:60)).
2. **graphrag offers a progress protocol we ignore.** `build_index(..., callbacks=[WorkflowCallbacks], verbose=bool)` yields `pipeline_start(names)`, `workflow_start/end(name)`, `progress(Progress)`. [graph.py:121](groundly/ingestion/graph.py:121) passes neither.
3. **The codebase has zero `logging` usage.** Greenfield — no existing convention to match, and no handler to collide with.

Also found, and fixed as part of this work: **`build_index` never raises on workflow failure.** It yields `PipelineRunResult(error=...)`, logs, calls `pipeline_error`, and returns normally (`graphrag/api/index.py:84-93`). `graph.py:121` discards the return value, so a partially-failed build prints `Graph built for X` *and* stamps the manifest with a fresh `corpus_hash` — marking a broken graph as current so `graph_is_stale` never triggers a rebuild.

Outcome: a `--debug` flag that streams graphrag's own logs plus Groundly's, a workflow-level progress bar for the normal case, and log lines at the handful of places that today degrade silently.

## Design

### Two hard constraints

- **Never stdout.** `groundly mcp` speaks the MCP protocol over stdout ([cli/mcp.py:13](groundly/cli/mcp.py:13)) and the CLI's rich `Console` ([cli/app.py:23](groundly/cli/app.py:23)) writes there. Every record goes to stderr.
- **Do not instrument `ingestion/extract_worker.py`.** The parent reads only the *last line* of worker stderr ([extract.py:47-51](groundly/ingestion/extract.py:47)) to build the user-facing cause. A log line there silently destroys error messages. Leave that file alone.

### `groundly/core/logs.py` (new, ~35 lines)

Named `logs.py`, not `logging.py`, to avoid any confusion with the stdlib module. A foundation, so `agents/`, `retrieval/`, `ingestion/`, and `llm/` may all import it without violating layering.

```python
def setup_logging(debug: bool = False) -> bool:
    """Attach one stderr handler to the ROOT logger; return True if logging is on
    (callers use that to disable live progress displays)."""
```

- Level resolution: `debug=True` → DEBUG; else `GROUNDLY_LOG_LEVEL` (via `logging.getLevelNamesMapping()`, 3.11+); else configure **nothing** and return `False`, so default behavior is byte-for-byte unchanged.
- Invalid `GROUNDLY_LOG_LEVEL` → `ValueError` naming the valid names (conventions.md: name the cause). CLI catches it via `_fail`.
- Handler goes on the **root** logger, formatter `"%(levelname)s %(name)s: %(message)s"`, `stream=sys.stderr`.
- Root logger's own level stays at WARNING. Only `groundly`, `graphrag`, `graphrag_llm` get `setLevel(level)`.
- Idempotent via a module-level `_configured` flag (`groundly serve` and the CLI must not double-attach).

**Why root and not the `graphrag` logger:** `init_loggers` *clears* handlers on `graphrag`/`graphrag_llm` before attaching its own, so anything attached beforehand is wiped — but it never sets `propagate = False`. A root handler still receives every record. Verified empirically: after simulating the clear, `graphrag.index.run` DEBUG and `groundly.agents.ask` INFO both reach the root handler, while `httpx` DEBUG does not (its effective level inherits root's WARNING, so the record is never created). Third-party noise is suppressed by construction, not by a filter.

**No log file.** Log lines carry query text and chunk ids — layer-4 data. The privacy boundary is a file (`.claude/rules/grounding-and-privacy.md`), so keeping logs ephemeral on stderr means there is no new artifact for export code to reason about. Never log full chunk text; ids and scores only.

### Graph build: progress bar + verbose

`ingestion/graph.py` — `build_graph` grows two keyword params, mirroring the callback contract already established in [ingestion/pipeline.py:120-128](groundly/ingestion/pipeline.py:120) (ingestion never does I/O itself):

```python
def build_graph(subj, store, *, estimated_tokens=0, estimated_cost_usd=None,
                on_event: Callable[[str, int, int], None] | None = None,
                verbose: bool = False) -> None:
```

`on_event(description, completed, total)` — plain types, so the CLI never imports graphrag. A private `_ProgressCallbacks(NoopWorkflowCallbacks)` adapter translates: `pipeline_start(names)` sets the total to `len(names)`, `workflow_start` sets the description, `workflow_end` advances, `pipeline_error` logs at ERROR with `exc_info`. Skip `progress()` — workflow-level granularity is enough for a bar, and graphrag's own `ProgressTicker` INFO lines cover sub-workflow detail under `--debug`.

Then `build_index(config, input_documents=df, callbacks=[adapter], verbose=verbose)`.

**Bug fix in the same change:** capture `results = asyncio.run(build_index(...))` and raise `GraphBuildError` naming the failed workflows if any `r.error is not None`, *before* the manifest is stamped at [graph.py:125-131](groundly/ingestion/graph.py:125). Add `logger.debug("graph build failed", exc_info=True)` before the existing `GraphBuildError` raise at :122 so `--debug` shows the real traceback that `from exc` currently buries.

`cli/subjects.py` — `_maybe_build_graph(subj, *, graph, yes, debug)` wraps the `build_graph` call in a `Progress` mirroring the indexing bar at [subjects.py:94-113](groundly/cli/subjects.py:94), with `disable=debug_on`. **`rich.progress.Progress` accepts `disable: bool = False`** (verified), which makes the whole live display a no-op while every `add_task`/`update`/`advance` call stays valid — so suppressing the bar under `--debug` needs one kwarg, not a restructured branch. Apply the same `disable=` to the existing indexing bar at :94.

### CLI surface

`--debug` on `index`, `ask`, `search`, `serve`; each calls `setup_logging(debug)` first and threads the returned bool where a live display needs disabling. `groundly mcp` calls `setup_logging()` with no flag — the host spawns it, so `GROUNDLY_LOG_LEVEL` is the only reachable switch. Help text: `"Stream debug logs to stderr (also: GROUNDLY_LOG_LEVEL=DEBUG)."`

No `config.toml` change — deliberately avoiding the duplicated `Settings(...)` construction at [config.py:117-121](groundly/core/config.py:117) and [:166-170](groundly/core/config.py:166), plus the hand-listed display at [cli/models.py:39-48](groundly/cli/models.py:39).

### Instrumentation — the silent degradations

Each module does `logger = logging.getLogger(__name__)` at import. **INFO = a real degradation the user would want to know about; DEBUG = routine detail.**

| Location | Level | What it says |
|---|---|---|
| [agents/ask.py:84](groundly/agents/ask.py:84), [:94](groundly/agents/ask.py:94) | INFO | Router picked a graph arm, `GraphNotBuiltError` forced vector-only. The single highest-value line in the repo — today the user gets silently worse results. |
| [agents/router.py:20](groundly/agents/router.py:20) | DEBUG | No router provider configured; no label. |
| [agents/router.py:24](groundly/agents/router.py:24) | INFO | Router unreachable; degraded to no label. |
| [agents/router.py:26](groundly/agents/router.py:26) | INFO | Unexpected reply coerced to `factoid` (log the raw reply). |
| [retrieval/vector.py:91-110](groundly/retrieval/vector.py:91) | DEBUG | Per-channel hit counts (dense/sparse/bm25), fused pool size, rerank on/off. |
| [retrieval/vector.py:110](groundly/retrieval/vector.py:110) | DEBUG | Final `path` + top `(chunk_id, score)` pairs — **scores exist nowhere else**; `record_trace` stores `chunk_ids` but no scores. |
| [retrieval/vector.py:114](groundly/retrieval/vector.py:114), [retrieval/graph.py:120](groundly/retrieval/graph.py:120) | DEBUG | Chunk vanished between fusion and detail lookup. |
| retrieval/graph.py local + global | DEBUG | Entity/community hit counts, resolved text-unit ids, `path`. |
| [agents/citations.py:31-34](groundly/agents/citations.py:31) | INFO | Count + ids of hallucinated citations dropped. |
| ingestion/graph.py | DEBUG/ERROR | Build start (chunk count, model), workflow errors, failure traceback. |

~13 calls. Everything else from the sweep — deck-parse failures, the `llm/chat.py:79-83` cost swallow, `embeddings.py` `cached_snapshot`, the `IntegrityError` pass, job tracebacks — is out of scope for this pass.

## Files

**New:** `groundly/core/logs.py`, `tests/core/test_logs.py`

**Modified:** `groundly/ingestion/graph.py` (callbacks adapter, `verbose`, `on_event`, results-error check), `groundly/cli/subjects.py` (`--debug`, graph bar, `disable=`), `groundly/cli/ask.py`, `groundly/cli/serve.py`, `groundly/cli/mcp.py` (`setup_logging`), `groundly/agents/ask.py`, `groundly/agents/router.py`, `groundly/agents/citations.py`, `groundly/retrieval/vector.py`, `groundly/retrieval/graph.py`

**Test files touched:** `tests/ingestion/test_ingestion_graph.py`, `tests/cli/test_cli_subjects.py`, `tests/agents/` (ask/router fallback assertions)

## Tests

`tests/core/test_logs.py`:
- Default is off: no handler added, no records emitted.
- `setup_logging(debug=True)` and `GROUNDLY_LOG_LEVEL=INFO` both attach; flag wins over env.
- Invalid `GROUNDLY_LOG_LEVEL` raises `ValueError` naming the valid names.
- Handler's `stream is sys.stderr` — the stdio-safety assertion (mirrors the kwarg-capture pattern at [tests/mcp/test_mcp_server.py:621-638](tests/mcp/test_mcp_server.py:621)).
- Two calls attach one handler.
- **The load-bearing propagation test:** after `setup_logging(debug=True)`, clear handlers on the `graphrag` logger the way `init_loggers` does, then assert a `graphrag.index.run` DEBUG record still reaches the handler and an `httpx` DEBUG record does not.

`tests/ingestion/test_ingestion_graph.py` (monkeypatched `build_index`, as existing tests already do):
- `callbacks=` and `verbose=` are passed through.
- The adapter turns `pipeline_start`/`workflow_start`/`workflow_end` into `on_event` calls with the right totals.
- A returned `PipelineRunResult` with `error` set raises `GraphBuildError` naming the workflow, **and leaves the manifest's `corpus_hash` unchanged**.

`tests/cli/test_cli_subjects.py`: `index --graph --debug` emits no bar and does emit log lines on stderr (`CliRunner(mix_stderr=False)` to separate the streams). Existing substring assertions on `result.output` must keep passing unchanged without the flag.

`tests/agents/`: with `caplog`, a `GraphNotBuiltError` fallback emits the INFO record and `ask` still returns a vector answer. (`caplog` is new to this repo — no existing usage.)

## Verification

```bash
.venv/bin/python -m pytest -q && .venv/bin/ruff check groundly tests && .venv/bin/ruff format --check groundly tests
```

Then the real end-to-end pass on a subject with an `[providers.extraction]` configured:

```bash
.venv/bin/groundly index THESIS ~/some/course/pdfs --graph --debug
```

Expect: cost estimate → confirm → graphrag's own INFO lines streaming on stderr (workflow names, `ProgressTicker` counts), no progress bar, and `~/.groundly/THESIS/graph/logs/indexing-engine.log` now at DEBUG.

Same build without `--debug`: a workflow-level bar advancing through graphrag's pipeline, then `Graph built for THESIS`.

```bash
.venv/bin/groundly ask THESIS "how do the three parts relate?" --debug
```

Expect, on a subject with **no** graph built: an INFO line stating the router chose `global`/`multi-hop` and the graph arm was unavailable, then the vector answer — the degradation that is invisible today.

Confirm stdio is intact:

```bash
GROUNDLY_LOG_LEVEL=DEBUG .venv/bin/groundly mcp < /dev/null
```

Logs on stderr only; stdout carries nothing but protocol.

## Out of scope

Deck-parse diagnostics ([agents/decks.py:100-125](groundly/agents/decks.py:100)), the cost-computation swallow ([llm/chat.py:79-83](groundly/llm/chat.py:79)), `cached_snapshot`'s double-`None` ([llm/embeddings.py:46-56](groundly/llm/embeddings.py:46)), the `IntegrityError` pass ([ingestion/pipeline.py:201](groundly/ingestion/pipeline.py:201)), job tracebacks ([agents/jobs.py:47](groundly/agents/jobs.py:47)), OCR-fired visibility, a `groundly traces` verb to read the trace rows nothing currently surfaces, and any `config.toml` log setting.

## Amendments from review

Three changes to the design above, all from the `spec-guardian` / `security-reviewer` pass. Each was verified empirically before the fix landed.

1. **`groundly/__init__.py` attaches a `NullHandler`** (not in the original plan). The plan claimed "configure nothing and return `False`, so default behavior is byte-for-byte unchanged" — false as written. Stdlib's `logging.lastResort` is a WARNING-level stderr handler that fires whenever no handler is found in the chain, so the one `logger.error` in `_ProgressCallbacks.pipeline_error` printed a raw traceback to stderr with logging *off*, breaking both that promise and `GraphBuildError`'s "no raw traceback ever surfaces" guarantee. A `NullHandler` on the package-root logger suppresses `lastResort` without affecting propagation to the root handler when logging is on. Pinned by two tests in `tests/core/test_logs.py`.

2. **`verbose` is gone from `build_graph`; graphrag stays at its own default INFO.** The plan passed `verbose=debug` through to `build_index`. That raises graphrag's loggers to DEBUG, and `graphrag/api/index.py:90` does `logger.debug(str(output.result))` — for the text-unit workflows `result` is a sample DataFrame *including the text column*, i.e. verbatim course material onto stderr and persisted into `<subject>/graph/logs/indexing-engine.log`. That violates the design's own "ids and scores only, never full chunk text" rule. INFO already carries the workflow names and `ProgressTicker` counts that `--debug` exists to surface, so dropping the parameter costs nothing and also resolves a second finding (`verbose` had been bound to "logging is on at any level", so `GROUNDLY_LOG_LEVEL=WARNING` would have enabled graphrag DEBUG).

3. **`_build_config` moved out of the broad `try`.** The config embeds the `extraction` provider's `api_key`, and a pydantic `ValidationError` echoes the offending input value — so the new `exc_info=True` debug log, and the pre-existing wrapped message, could both carry the key. It now has its own guard raising a `GraphBuildError` that names the cause without interpolating the exception.

Two fixes outside the plan's stated scope, both directly on the stdout boundary the design is built on:

- **`llm/chat.py` sets `litellm.suppress_debug_info = True`.** litellm `print()`s an ANSI-coloured "Give Feedback / Get Help" banner to **stdout** on any provider exception (verified: 116 bytes). `groundly mcp` speaks MCP over stdout and calls `complete()` in-process, so an unreachable LM Studio corrupted the JSON-RPC stream. Pre-existing, but it falsified this design's hard constraint, so it is fixed here.
- **`ingestion/extract.py` pops `GROUNDLY_LOG_LEVEL` from the extraction worker's env.** The parent reads only the *last line* of worker stderr to name the failure cause, so the "don't instrument the worker" rule was protected by convention alone; now it's structural.

Smaller: `cli/mcp.py` prints a named cause to stderr and exits 1 on a bad `GROUNDLY_LOG_LEVEL` rather than surfacing a rich traceback (verified: stdout stays 0 bytes); the router's logged raw reply is truncated to 80 chars.

## Execution

Branch `logging` off `verified-cards`. This plan gets committed to `docs/superpowers/specs/2026-07-25-debug-logging-design.md` as the first commit on the branch, matching the convention used for the P5 and P6 slices. Implementation by a Sonnet subagent; `spec-guardian` and `security-reviewer` before commit (the stdio/stdout boundary and the no-log-file privacy decision are exactly their beat).
