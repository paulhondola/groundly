"""The topic-tree and test page server on loopback and, optionally, Tailscale.

Titles, summaries, questions and chunk text come from stores that an imported bundle may have
written. The page manages remote sessions in `remote.db` and writes attempts to the student's `progress.db`,
through `agents/quiz.py`, and only from a JSON POST carrying the page's own Origin; it never
writes store.db and loads no model; under `groundly start` the same process also serves
the MCP tools at `/mcp`, whose first search loads the models. Two more page POSTs start
and stop a run of the student's own Claude Code (`runs.py`); that run writes only through the
MCP gates, never through this module. A stored filename is never trusted as a path: a material is
addressed by id and its file must resolve inside the subject's materials dir.
"""

import asyncio
import atexit
import json
import sqlite3
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, TypeVar

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.routing import Route
from starlette.types import ASGIApp

from groundly.agents import quiz
from groundly.core.paths import discover_subjects, remote_db_path
from groundly.core.progress import ProgressStore
from groundly.core.store import SubjectStore
from groundly.core.subject import Subject
from groundly.web import runs
from groundly.web.remote import (
    SESSION_COOKIE,
    SESSION_TTL_S,
    PairAuthError,
    RemoteRuntime,
    RemoteStore,
    _SocketPerimeterMiddleware,
    qr_matrix,
    remote_db_error,
)
from groundly.web.runs import Runner
from groundly.web.schemas import (
    AttemptResultResponse,
    AttemptSummary,
    AuthStatusResponse,
    CheckAnswerRequest,
    CheckAnswerResponse,
    DrawTestResponse,
    ErrorResponse,
    PairDeviceRequest,
    PairDeviceResponse,
    RemoteInfoResponse,
    RevokeAllResponse,
    RevokeDeviceResponse,
    RunsInfoResponse,
    RunSnapshot,
    StartRunRequest,
    StopRunRequest,
    StopRunResponse,
    SubjectSummary,
    SubmitAttemptOpenAPIBody,
    SubmitAttemptRequest,
    SubmitAttemptResponse,
    TreeResponse,
)

_WEB = Path(__file__).parent
_PAGE = (_WEB / "page.html").read_text(encoding="utf-8")

# HTML responses only: the browser's own PDF viewer needs the PDF response left unrestricted.
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; "
    "font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)

# 20 answers of at most 4000 characters, with room for option ids and JSON escapes.
MAX_BODY_BYTES = 256 * 1024

# The names the page answers to; the Host check against DNS rebinding. Fixed, never configurable.
HOSTS = ["127.0.0.1", "localhost", "groundly.localhost"]

T = TypeVar("T")


class _PageJsonError(Exception):
    """Pre-parse refusal raised before Pydantic model validation runs."""

    def __init__(self, error: str, detail: str, status_code: int = 400) -> None:
        super().__init__(detail)
        self.error = error
        self.detail = detail
        self.status_code = status_code


def _open_subject(name: str) -> Subject | None:
    try:
        subject = Subject(name)
    except ValueError:
        return None
    return subject if subject.exists() else None


def _read(subject: Subject, reader: Callable[[SubjectStore], T]) -> T | JSONResponse:
    """Run a store read. A damaged or incomplete store answers with its own message."""
    try:
        return reader(SubjectStore(subject.store_db_path))
    except (sqlite3.DatabaseError, RuntimeError) as e:
        return JSONResponse({"error": str(e)}, status_code=500)


def _html(status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(_PAGE, status_code=status_code, headers={"Content-Security-Policy": _CSP})


def _stored_pdf(subject: Subject, filename: object) -> Path | None:
    path = subject.stored_material(filename)
    return path if path is not None and path.suffix.lower() == ".pdf" else None


def _error(error: str, detail: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"error": error, "detail": detail}, status_code=status_code)


def _quiz(
    subject_name: str,
    call: Callable[[SubjectStore, ProgressStore], Any],
    subject: Subject | None = None,
) -> Any:
    """Run a quiz call on the subject's two stores, mapping each failure to its own answer.
    A caller that already opened the subject passes it in."""
    if subject is None:
        subject = _open_subject(subject_name)
    if subject is None:
        return _error("unknown_subject", f"unknown subject {subject_name!r}", 404)
    try:
        return call(SubjectStore(subject.store_db_path), ProgressStore(subject.progress_db_path))
    except quiz.QuizError as e:
        return _error(e.reason, e.detail)
    except sqlite3.OperationalError as e:
        if "locked" in str(e) or "busy" in str(e):
            return _error(
                "busy",
                "the subject's database is busy — another groundly process is writing; "
                "try again in a moment",
                503,
            )
        return _error("store_error", str(e), 500)
    except (sqlite3.DatabaseError, RuntimeError) as e:
        return _error("store_error", str(e), 500)


async def _read_body(request: Request) -> bytes | None:
    """The request body, or None once it passes MAX_BODY_BYTES; never buffers more."""
    try:
        content_length = int(request.headers.get("content-length") or 0)
    except ValueError:
        content_length = 0
    if content_length > MAX_BODY_BYTES:
        return None
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            return None
    return body


async def _parse_page_json(request: Request, forbidden: str) -> object:
    """Enforce Origin -> Content-Type -> 256 KiB cap -> JSON decode for page POSTs."""
    origin = request.headers.get("origin")
    expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
    if origin is None or origin.lower() != expected.lower():
        raise _PageJsonError("forbidden", forbidden, 403)
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        raise _PageJsonError("unsupported", "the body is JSON", 415)
    raw = await _read_body(request)
    if raw is None:
        raise _PageJsonError("too_large", "the body is larger than a test can be", 413)
    try:
        return json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise _PageJsonError("invalid_body", "the body is not valid JSON", 400) from exc


def _require_page_json(forbidden: str) -> Callable[[Request], Any]:
    """Dependency enforcing Origin -> Content-Type -> 256 KiB cap -> JSON decode."""

    async def dependency(request: Request) -> object:
        return await _parse_page_json(request, forbidden)

    return dependency


_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse} for code in (400, 401, 403, 404, 409, 413, 415, 429, 500, 503)
}


def _error_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    return {code: _ERROR_RESPONSES[code] for code in codes}


def _protected_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    return _error_responses(401, 500, 503, *codes)


def _json_body_schema(model: type[BaseModel]) -> dict[str, Any]:
    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": model.model_json_schema(ref_template="#/components/schemas/{model}")
                }
            },
        }
    }


def _hoist_body_defs(schema: dict[str, Any]) -> None:
    """Move Pydantic $defs from inline requestBody schemas into components.schemas."""
    schemas = schema.setdefault("components", {}).setdefault("schemas", {})
    for path_item in schema.get("paths", {}).values():
        if not isinstance(path_item, dict):
            continue
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            body_schema = (
                operation.get("requestBody", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema")
            )
            if isinstance(body_schema, dict) and "$defs" in body_schema:
                defs = body_schema.pop("$defs")
                if isinstance(defs, dict):
                    schemas.update(defs)


class _GroundlyApp(FastAPI):
    """FastAPI subclass that hoists inline requestBody $defs into components.schemas."""

    def openapi(self) -> dict[str, Any]:
        schema = super().openapi()
        _hoist_body_defs(schema)
        return schema


def _active_remote_store(remote: RemoteRuntime | None) -> RemoteStore | None:
    """Return the active RemoteStore, or open remote.db only if it already exists."""
    if remote is not None:
        return remote.store
    return RemoteStore() if remote_db_path().exists() else None


def create_app(
    mcp_app: ASGIApp | None = None,
    mcp_url: str | None = None,
    remote: RemoteRuntime | None = None,
) -> FastAPI:
    """The page; with `mcp_app` (FastMCP's HTTP app, routed at `/mcp`) mounted after it, and
    `mcp_url` its address, which a page run uses instead of starting its own Groundly."""
    runner = Runner(mcp_url)
    atexit.register(runner.shutdown)  # a forced exit (a second Ctrl-C) skips the lifespan

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            mcp_router = getattr(mcp_app, "router", None)
            if mcp_router is None:
                yield
            else:  # the mounted app's session manager runs in its lifespan
                async with mcp_router.lifespan_context(app):
                    yield
        finally:
            runner.shutdown()  # no `claude` outlives the page

    app = _GroundlyApp(
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
        middleware=[
            Middleware(
                _SocketPerimeterMiddleware,
                loopback_hosts=HOSTS,
                html_shell=_html,
                remote=remote,
            )
        ],
    )
    app.state.runner = runner

    @app.exception_handler(_PageJsonError)
    async def _page_json_error_handler(_: Request, exc: _PageJsonError) -> JSONResponse:
        return _error(exc.error, exc.detail, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        detail = str(first.get("msg") or "invalid request parameter")
        return _error("invalid_text", detail, 400)

    @app.get("/", include_in_schema=False)
    def _root() -> HTMLResponse:
        return _html()

    @app.get("/pair", include_in_schema=False)
    def _pair_page() -> HTMLResponse:
        return _html()

    app.mount("/static", StaticFiles(directory=_WEB / "static"), name="static")

    @app.get("/s/{subject}", include_in_schema=False)
    def _subject_page(subject: str) -> HTMLResponse:
        # An unknown subject still gets the page: its tree request fails and the page shows why.
        known = _open_subject(subject) is not None
        return _html(status_code=200 if known else 404)

    @app.get("/s/{subject}/test", include_in_schema=False)
    def _test_page(subject: str) -> HTMLResponse:
        return _subject_page(subject)

    @app.get("/s/{subject}/attempts/{id:int}", include_in_schema=False)
    def _attempt_page(subject: str, id: int) -> HTMLResponse:
        return _subject_page(subject)

    @app.get("/s/{subject}/materials/{id:int}", include_in_schema=False)
    def _material(subject: str, id: int) -> Response:
        opened = _open_subject(subject)
        if opened is None:
            return Response("unknown subject", status_code=404)
        if not quiz.is_id(id):
            return Response("unknown material", status_code=404)
        filename = _read(opened, lambda store: store.material_filename(id))
        if isinstance(filename, Response):
            return filename
        if filename is None:
            return Response("unknown material", status_code=404)
        path = _stored_pdf(opened, filename)
        if path is None:
            return Response("not a stored pdf", status_code=404)
        return FileResponse(
            path,
            media_type="application/pdf",
            headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"},
        )

    @app.get(
        "/api/subjects",
        response_model=list[SubjectSummary],
        response_model_exclude_none=True,
        responses=_protected_responses(),
        operation_id="listSubjects",
    )
    def _subjects() -> list[dict[str, Any]]:
        out = []
        for name in discover_subjects():
            subject = _open_subject(name)
            if subject is None:
                continue
            entry: dict[str, Any] = {
                "subject": name,
                "title": subject.display_title(),
                "has_tree": False,
            }
            try:
                entry["has_tree"] = bool(SubjectStore(subject.store_db_path).topic_ids())
            except (sqlite3.DatabaseError, RuntimeError) as e:
                entry["error"] = str(e)
            out.append(entry)
        return out

    @app.get(
        "/api/subjects/{subject}/tree",
        response_model=TreeResponse,
        responses=_protected_responses(404),
        operation_id="getSubjectTree",
    )
    def _tree(subject: str) -> Any:
        opened = _open_subject(subject)
        if opened is None:
            return JSONResponse({"error": f"unknown subject {subject!r}"}, status_code=404)

        def read(store: SubjectStore) -> dict[str, Any]:
            tree = store.tree()
            try:
                states = quiz.mastery(store, ProgressStore(opened.progress_db_path), tree)
            except (sqlite3.DatabaseError, RuntimeError):
                states = {}  # an unreadable progress.db costs the progress lines, not the map
            return {"title": opened.display_title(), "tree": tree, "progress": states}

        result = _read(opened, read)
        if isinstance(result, Response):
            return result
        # `pdf` tells the page whether a link would open: a bundle exported without its
        # materials still cites PDFs it cannot serve.
        openable: dict[int, bool] = {}

        states = result.pop("progress")

        def flag(nodes: list[dict[str, Any]]) -> None:
            for node in nodes:
                node["progress"] = states.get(node["id"])
                for src in node["sources"]:
                    if src["material_id"] not in openable:
                        openable[src["material_id"]] = (
                            _stored_pdf(opened, src["filename"]) is not None
                        )
                    src["pdf"] = openable[src["material_id"]]
                flag(node["subtopics"])

        flag(result["tree"])
        return result

    @app.get(
        "/api/subjects/{subject}/test",
        response_model=DrawTestResponse,
        responses=_protected_responses(400, 404),
        operation_id="drawTest",
    )
    def _draw(subject: str, topic: int | None = None, n: int = 10) -> Any:
        return _quiz(
            subject,
            lambda store, progress: {"questions": quiz.draw(store, progress, topic, n)},
        )

    @app.post(
        "/api/subjects/{subject}/attempts",
        status_code=201,
        response_model=SubmitAttemptResponse,
        responses=_protected_responses(400, 403, 404, 413, 415),
        openapi_extra=_json_body_schema(SubmitAttemptOpenAPIBody),
        operation_id="submitAttempt",
    )
    async def _submit(
        subject: str,
        body: object = Depends(_require_page_json("a test is submitted from this page only")),
    ) -> Any:
        try:
            payload = SubmitAttemptRequest.model_validate(body)
        except ValidationError:
            return _error("invalid_body", "expected {token, topic_id, answers}")

        def submit(store: SubjectStore, progress: ProgressStore) -> dict[str, int]:
            attempt_id = quiz.submit_attempt(
                store, progress, payload.token, payload.topic_id, payload.answers
            )
            return {"id": attempt_id}

        return await run_in_threadpool(_quiz, subject, submit)

    @app.post(
        "/api/subjects/{subject}/questions/{id}/check",
        response_model=CheckAnswerResponse,
        responses=_protected_responses(400, 403, 404, 415),
        openapi_extra=_json_body_schema(CheckAnswerRequest),
        operation_id="checkAnswer",
    )
    async def _check(
        subject: str,
        id: int,
        body: object = Depends(_require_page_json("an answer is checked from this page only")),
    ) -> Any:
        try:
            payload = CheckAnswerRequest.model_validate(body)
        except ValidationError:
            return _error("invalid_body", "expected {selected}")
        opened = _open_subject(subject)
        if opened is None:
            return _error("unknown_subject", f"unknown subject {subject!r}", 404)

        def check(store: SubjectStore, _progress: ProgressStore) -> Any:
            # ponytail: the lock on a checked answer lives in the page; a hand-made call could
            # check, then submit another choice. One user, the student; a server lock would
            # need per-test state and a write before submit.
            try:
                result = quiz.check_answer(store, id, payload.selected)
            except quiz.QuizError as e:
                if e.reason != "unknown_question":
                    raise
                return _error(e.reason, e.detail, 404)
            for src in result["sources"]:
                src["pdf"] = _stored_pdf(opened, src["filename"]) is not None
            return result

        return await run_in_threadpool(_quiz, subject, check, opened)

    @app.get(
        "/api/subjects/{subject}/attempts",
        response_model=list[AttemptSummary],
        responses=_protected_responses(400, 404),
        operation_id="listAttempts",
    )
    def _history(subject: str) -> Any:
        return _quiz(subject, quiz.attempt_history)

    @app.get(
        "/api/subjects/{subject}/attempts/{id:int}",
        response_model=AttemptResultResponse,
        responses=_protected_responses(400, 404),
        operation_id="getAttemptResult",
    )
    def _result(subject: str, id: int) -> Any:
        opened = _open_subject(subject)
        if opened is None:
            return _error("unknown_subject", f"unknown subject {subject!r}", 404)

        def read(store: SubjectStore, progress: ProgressStore) -> Any:
            result = quiz.attempt_result(store, progress, id) if quiz.is_id(id) else None
            if result is None:
                return _error("unknown_attempt", "no such attempt", 404)
            for answer in result["answers"]:
                for src in answer["sources"]:
                    src["pdf"] = _stored_pdf(opened, src["filename"]) is not None
            return result

        return _quiz(subject, read, opened)

    @app.get(
        "/api/runs",
        response_model=RunsInfoResponse,
        responses=_protected_responses(),
        operation_id="getRuns",
    )
    def _runs(request: Request) -> dict[str, Any]:
        _, reason = runs.availability()
        return {
            "available": reason is None,
            "reason": reason,
            "target": runs.TARGET_QUESTIONS,
            "run": request.app.state.runner.view(),
        }

    @app.get("/api/runs/stream", responses=_protected_responses(), operation_id="streamRuns")
    async def _stream_runs(request: Request) -> StreamingResponse:
        active_runner: Runner = request.app.state.runner
        loop = asyncio.get_running_loop()
        is_ts_stream = (
            remote is not None and getattr(request.state, "access", "loopback") == "tailscale"
        )
        session_token = request.cookies.get(SESSION_COOKIE) if is_ts_stream else None

        async def _session_revoked() -> bool:
            if not is_ts_stream or remote is None:
                return False
            if not session_token:
                return True
            try:
                return not await run_in_threadpool(remote.store.authenticate, session_token)
            except (sqlite3.DatabaseError, RuntimeError):
                return True

        async def events():
            revoke_event = asyncio.Event() if is_ts_stream and remote is not None else None
            queue = None
            try:
                if revoke_event is not None and remote is not None:
                    remote.register_stream(loop, revoke_event)
                snapshot, queue = active_runner.subscribe(loop)
                if await _session_revoked():
                    return
                next_auth_check = loop.time() + runs.SSE_KEEPALIVE_S
                yield f"event: snapshot\ndata: {json.dumps(snapshot)}\n\n"
                if queue is None or snapshot is None or snapshot["status"] != "running":
                    return
                while True:
                    if revoke_event is None:
                        try:
                            item = await asyncio.wait_for(queue.get(), timeout=runs.SSE_KEEPALIVE_S)
                        except TimeoutError:
                            if await request.is_disconnected():
                                break
                            yield ": keep-alive\n\n"
                            continue
                    else:
                        get_task = asyncio.create_task(queue.get())
                        revoke_task = asyncio.create_task(revoke_event.wait())
                        wait_tasks: tuple[asyncio.Task[Any], asyncio.Task[Any]] = (
                            get_task,
                            revoke_task,
                        )
                        try:
                            done, pending = await asyncio.wait(
                                wait_tasks,
                                timeout=max(0, next_auth_check - loop.time()),
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                        except BaseException:
                            for task in wait_tasks:
                                if not task.done():
                                    task.cancel()
                            await asyncio.gather(*wait_tasks, return_exceptions=True)
                            raise
                        for task in pending:
                            task.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)
                        if revoke_task in done or loop.time() >= next_auth_check:
                            revoke_event.clear()
                            if await _session_revoked():
                                break
                            next_auth_check = loop.time() + runs.SSE_KEEPALIVE_S
                        if get_task not in done:
                            if await request.is_disconnected():
                                break
                            yield ": keep-alive\n\n"
                            continue
                        item = get_task.result()
                    if item is None:
                        break
                    event_name, payload = item
                    yield f"event: {event_name}\ndata: {json.dumps(payload)}\n\n"
                    if event_name == "done" or (
                        event_name == "snapshot"
                        and (payload is None or payload.get("status") != "running")
                    ):
                        break
            finally:
                if queue is not None:
                    active_runner.unsubscribe(loop, queue)
                if revoke_event is not None and remote is not None:
                    remote.unregister_stream(loop, revoke_event)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post(
        "/api/subjects/{subject}/runs",
        status_code=202,
        response_model=RunSnapshot,
        responses=_protected_responses(400, 403, 404, 409, 413, 415),
        openapi_extra=_json_body_schema(StartRunRequest),
        operation_id="startRun",
    )
    async def _start_run(
        subject: str,
        request: Request,
        body: object = Depends(_require_page_json("a run is started from this page only")),
    ) -> Any:
        """Start one of the two chores. The body carries ids only; the prompt is Groundly's own."""
        try:
            payload = StartRunRequest.model_validate(body)
        except ValidationError:
            return _error("invalid_body", 'expected {kind: "write", topic_id} or {kind: "grade"}')
        active_runner: Runner = request.app.state.runner

        def start(store: SubjectStore, progress: ProgressStore) -> Any:
            topic_id, big = payload.topic_id, False
            if payload.kind == "write":
                assert topic_id is not None  # guaranteed by StartRunRequest._validate_scope
                leaves = quiz.scope_leaves(store, topic_id)
                bank = store.question_ids_by_topic()
                if all(len(bank.get(leaf, ())) >= runs.TARGET_QUESTIONS for leaf in leaves):
                    detail = f"every topic in scope has {runs.TARGET_QUESTIONS} or more questions"
                    return _error("nothing_to_write", detail)
                big = leaves != [topic_id]
            elif not quiz.pending_reviews(store, progress)["awaiting"]:
                return _error("nothing_to_grade", "no written answer is awaiting review")
            try:
                return active_runner.start(
                    payload.kind,
                    subject,
                    topic_id,
                    runs.build_prompt(payload.kind, subject, topic_id, big),
                )
            except runs.RunError as e:
                return _error(e.reason, e.detail, e.status)

        # Off the event loop: SQL and a fork/exec, while the same loop may serve /mcp.
        return await run_in_threadpool(_quiz, subject, start)

    @app.post(
        "/api/runs/stop",
        response_model=StopRunResponse,
        responses=_protected_responses(400, 403, 413, 415),
        openapi_extra=_json_body_schema(StopRunRequest),
        operation_id="stopRun",
    )
    async def _stop_run(
        request: Request,
        body: object = Depends(_require_page_json("a run is stopped from this page only")),
    ) -> Any:
        try:
            StopRunRequest.model_validate(body)
        except ValidationError:
            return _error("invalid_body", "expected {}")
        # Stopping waits for the process to exit; off the event loop, which may be serving the
        # run's own /mcp calls under `groundly start`.
        return {"run": await run_in_threadpool(request.app.state.runner.stop)}

    @app.get(
        "/api/auth",
        response_model=AuthStatusResponse,
        responses=_error_responses(500, 503),
        operation_id="getAuthStatus",
    )
    def _auth_status(request: Request) -> dict[str, Any]:
        access = getattr(request.state, "access", "loopback")
        paired = bool(getattr(request.state, "paired", access == "loopback"))
        paired_devices = 0
        if access == "loopback":
            store = _active_remote_store(remote)
            if store is not None:
                try:
                    paired_devices = store.count_devices()
                except (sqlite3.DatabaseError, RuntimeError):
                    paired_devices = 0
        return {
            "access": access,
            "paired": paired,
            "tailscale_enabled": remote is not None,
            "paired_devices": paired_devices,
        }

    @app.post(
        "/api/pair",
        response_model=PairDeviceResponse,
        responses=_error_responses(400, 401, 403, 404, 413, 415, 429, 500, 503),
        openapi_extra=_json_body_schema(PairDeviceRequest),
        operation_id="pairDevice",
    )
    async def _pair_device(request: Request) -> Any:
        if remote is None or getattr(request.state, "access", "loopback") != "tailscale":
            return _error("not_available", "pairing is only used on Tailscale", 404)
        body = await _parse_page_json(request, "pairing is only allowed from this page")
        try:
            payload = PairDeviceRequest.model_validate(body)
        except ValidationError:
            return _error("invalid_body", "expected {token}")
        peer_ip = request.client.host if request.client is not None else "unknown"
        try:
            raw_session = await run_in_threadpool(
                remote.pair_device,
                payload.token,
                peer_ip,
                request.headers.get("user-agent", ""),
                request.cookies.get(SESSION_COOKIE),
            )
        except PairAuthError as e:
            return _error(e.error, e.detail, e.status_code)
        except (sqlite3.DatabaseError, RuntimeError) as exc:
            return remote_db_error(exc)
        response = JSONResponse({"paired": True}, status_code=200)
        response.set_cookie(
            key=SESSION_COOKIE,
            value=raw_session,
            max_age=SESSION_TTL_S,
            path="/",
            httponly=True,
            samesite="strict",
            secure=False,
        )
        return response

    @app.get(
        "/api/remote",
        response_model=RemoteInfoResponse,
        responses=_error_responses(404, 500, 503),
        operation_id="getRemoteInfo",
    )
    def _remote_info() -> Any:
        store = _active_remote_store(remote)
        try:
            devices = store.list_devices() if store is not None else []
        except (sqlite3.DatabaseError, RuntimeError) as exc:
            return remote_db_error(exc)
        if remote is None:
            return {
                "active": False,
                "pairing_url": None,
                "qr_matrix": None,
                "devices": devices,
            }
        pairing_url = remote.pairing_url
        return {
            "active": True,
            "pairing_url": pairing_url,
            "qr_matrix": qr_matrix(pairing_url),
            "devices": devices,
        }

    @app.post(
        "/api/remote/devices/{id:int}/revoke",
        response_model=RevokeDeviceResponse,
        responses=_error_responses(400, 403, 404, 413, 415, 500, 503),
        openapi_extra=_json_body_schema(StopRunRequest),
        operation_id="revokeRemoteDevice",
    )
    async def _revoke_remote_device(
        id: int,
        body: object = Depends(
            _require_page_json("device revocation is allowed from this page only")
        ),
    ) -> Any:
        try:
            StopRunRequest.model_validate(body)
        except ValidationError:
            return _error("invalid_body", "expected {}")
        if not quiz.is_id(id):
            return _error("unknown_device", "no such device", 404)

        def revoke() -> Any:
            try:
                store = _active_remote_store(remote)
                revoked = store.revoke_device(id) if store is not None else False
            except (sqlite3.DatabaseError, RuntimeError) as exc:
                return remote_db_error(exc)
            if revoked and remote is not None:
                remote.notify_revoked()
            return {"revoked": revoked}

        return await run_in_threadpool(revoke)

    @app.post(
        "/api/remote/revoke-all",
        response_model=RevokeAllResponse,
        responses=_error_responses(400, 403, 404, 413, 415, 500, 503),
        openapi_extra=_json_body_schema(StopRunRequest),
        operation_id="revokeAllRemoteDevices",
    )
    async def _revoke_all_remote_devices(
        body: object = Depends(
            _require_page_json("device revocation is allowed from this page only")
        ),
    ) -> Any:
        try:
            StopRunRequest.model_validate(body)
        except ValidationError:
            return _error("invalid_body", "expected {}")

        def revoke_all() -> Any:
            try:
                if remote is not None:
                    count = remote.revoke_all()
                else:
                    store = _active_remote_store(None)
                    count = store.revoke_all() if store is not None else 0
            except (sqlite3.DatabaseError, RuntimeError) as exc:
                return remote_db_error(exc)
            return {"revoked": count}

        return await run_in_threadpool(revoke_all)

    if mcp_app is not None:
        # A Route, not Mount(""): a catch-all mount would answer wrong-method page requests
        # with 404 instead of their 405. FastMCP's own Host/Origin guard still runs inside.
        app.router.routes.append(Route("/mcp", mcp_app))

    return app
