"""The topic-tree and test page server: loopback, one process for every subject.

Titles, summaries, questions and chunk text come from stores that an imported bundle may have
written. The page writes one thing, a submitted test attempt to the student's `progress.db`,
through `agents/quiz.py`, and only from a JSON POST carrying the page's own Origin; it never
writes store.db and loads no model; under `groundly start` the same process also serves
the MCP tools at `/mcp`, whose first search loads the models. Two more page POSTs start
and stop a run of the student's own Claude Code (`runs.py`); that run writes only through the
MCP gates, never through this module. A stored filename is never trusted as a path: a material is
addressed by id and its file must resolve inside the subject's materials dir.
"""

import atexit
import json
import re
import sqlite3
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TypeVar

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from groundly.agents import quiz
from groundly.core.paths import discover_subjects
from groundly.core.progress import ProgressStore
from groundly.core.store import SubjectStore
from groundly.core.subject import Subject
from groundly.web.runs import TARGET_QUESTIONS, RunError, Runner, availability, build_prompt

_WEB = Path(__file__).parent
_PAGE = (_WEB / "page.html").read_text(encoding="utf-8")

# HTML responses only: the browser's own PDF viewer needs the PDF response left unrestricted.
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


T = TypeVar("T")


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


def _html(_: Request, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(_PAGE, status_code=status_code, headers={"Content-Security-Policy": _CSP})


def _subject_page(request: Request) -> Response:
    # An unknown subject still gets the page: its tree request fails and the page shows why.
    known = _open_subject(request.path_params["subject"]) is not None
    return _html(request, status_code=200 if known else 404)


def _subjects(_: Request) -> JSONResponse:
    out = []
    for name in discover_subjects():
        subject = _open_subject(name)
        if subject is None:
            continue
        entry: dict = {"subject": name, "title": subject.display_title(), "has_tree": False}
        try:
            entry["has_tree"] = bool(SubjectStore(subject.store_db_path).topic_ids())
        except (sqlite3.DatabaseError, RuntimeError) as e:
            entry["error"] = str(e)
        out.append(entry)
    return JSONResponse(out)


def _stored_pdf(subject: Subject, filename) -> Path | None:
    path = subject.stored_material(filename)
    return path if path is not None and path.suffix.lower() == ".pdf" else None


def _tree(request: Request) -> Response:
    subject = _open_subject(request.path_params["subject"])
    if subject is None:
        name = request.path_params["subject"]
        return JSONResponse({"error": f"unknown subject {name!r}"}, status_code=404)

    def read(store: SubjectStore) -> dict:
        tree = store.tree()
        try:
            states = quiz.mastery(store, ProgressStore(subject.progress_db_path), tree)
        except (sqlite3.DatabaseError, RuntimeError):
            states = {}  # an unreadable progress.db costs the progress lines, not the map
        return {"title": subject.display_title(), "tree": tree, "progress": states}

    result = _read(subject, read)
    if isinstance(result, Response):
        return result
    # `pdf` tells the page whether a link would open: a bundle exported without its
    # materials still cites PDFs it cannot serve.
    openable: dict[int, bool] = {}

    states = result.pop("progress")

    def flag(nodes: list[dict]) -> None:
        for node in nodes:
            node["progress"] = states.get(node["id"])
            for src in node["sources"]:
                if src["material_id"] not in openable:
                    openable[src["material_id"]] = _stored_pdf(subject, src["filename"]) is not None
                src["pdf"] = openable[src["material_id"]]
            flag(node["subtopics"])

    flag(result["tree"])
    return JSONResponse(result)


def _material(request: Request) -> Response:
    subject = _open_subject(request.path_params["subject"])
    if subject is None:
        return Response("unknown subject", status_code=404)
    filename = _read(subject, lambda store: store.material_filename(request.path_params["id"]))
    if isinstance(filename, Response):
        return filename
    if filename is None:
        return Response("unknown material", status_code=404)
    path = _stored_pdf(subject, filename)
    if path is None:
        return Response("not a stored pdf", status_code=404)
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"},
    )


_TOKEN = re.compile(r"[A-Za-z0-9-]{8,64}")


def _error(error: str, detail: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"error": error, "detail": detail}, status_code=status_code)


def _quiz(
    request: Request,
    call: Callable[[SubjectStore, ProgressStore], Response],
    subject: Subject | None = None,
) -> Response:
    """Run a quiz call on the subject's two stores, mapping each failure to its own answer.
    A caller that already opened the subject passes it in."""
    if subject is None:
        subject = _open_subject(request.path_params["subject"])
    if subject is None:
        name = request.path_params["subject"]
        return _error("unknown_subject", f"unknown subject {name!r}", 404)
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


def _draw(request: Request) -> Response:
    params = request.query_params
    try:
        topic = int(params["topic"]) if "topic" in params else None
        n = int(params.get("n", "10"))
    except ValueError:
        return _error("invalid_text", "topic and n are whole numbers")
    return _quiz(
        request,
        lambda store, progress: JSONResponse({"questions": quiz.draw(store, progress, topic, n)}),
    )


# 20 answers of at most 4000 characters, with room for option ids and JSON escapes.
MAX_BODY_BYTES = 256 * 1024


async def _read_body(request: Request) -> bytes | None:
    """The request body, or None once it passes MAX_BODY_BYTES; never buffers more."""
    if int(request.headers.get("content-length") or 0) > MAX_BODY_BYTES:
        return None
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            return None
    return body


async def _page_json(request: Request, forbidden: str) -> object | Response:
    """The JSON body of a write from this page, or the refusal. The page's own Origin and a JSON
    content type keep other sites out: a cross-site JSON POST needs a preflight we never grant."""
    origin = request.headers.get("origin")
    if origin != f"{request.url.scheme}://{request.headers.get('host')}":
        return _error("forbidden", forbidden, 403)
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        return _error("unsupported", "the body is JSON", 415)
    raw = await _read_body(request)
    if raw is None:
        return _error("too_large", "the body is larger than a test can be", 413)
    try:
        return json.loads(raw)
    except (ValueError, RecursionError):
        return _error("invalid_body", "the body is not valid JSON")


async def _submit(request: Request) -> Response:
    body = await _page_json(request, "a test is submitted from this page only")
    if isinstance(body, Response):
        return body
    if (
        not isinstance(body, dict)
        or not isinstance(body.get("token"), str)
        or not _TOKEN.fullmatch(body["token"])
        or not (body.get("topic_id") is None or quiz.is_id(body["topic_id"]))
        or not isinstance(body.get("answers"), list)
    ):
        return _error("invalid_body", "expected {token, topic_id, answers}")

    def submit(store: SubjectStore, progress: ProgressStore) -> Response:
        attempt_id = quiz.submit_attempt(
            store, progress, body["token"], body.get("topic_id"), body["answers"]
        )
        return JSONResponse({"id": attempt_id}, status_code=201)

    return _quiz(request, submit)


def _history(request: Request) -> Response:
    return _quiz(
        request, lambda store, progress: JSONResponse(quiz.attempt_history(store, progress))
    )


def _result(request: Request) -> Response:
    name = request.path_params["subject"]
    subject = _open_subject(name)
    if subject is None:
        return _error("unknown_subject", f"unknown subject {name!r}", 404)

    def read(store: SubjectStore, progress: ProgressStore) -> Response:
        attempt_id = request.path_params["id"]
        result = (
            quiz.attempt_result(store, progress, attempt_id) if quiz.is_id(attempt_id) else None
        )
        if result is None:
            return _error("unknown_attempt", "no such attempt", 404)
        for answer in result["answers"]:
            for src in answer["sources"]:
                src["pdf"] = _stored_pdf(subject, src["filename"]) is not None
        return JSONResponse(result)

    return _quiz(request, read, subject)


def _runs(request: Request) -> JSONResponse:
    _, reason = availability()
    return JSONResponse(
        {
            "available": reason is None,
            "reason": reason,
            "target": TARGET_QUESTIONS,
            "run": request.app.state.runner.view(),
        }
    )


async def _start_run(request: Request) -> Response:
    """Start one of the two chores. The body carries ids only; the prompt is Groundly's own."""
    body = await _page_json(request, "a run is started from this page only")
    if isinstance(body, Response):
        return body
    kind = body.get("kind") if isinstance(body, dict) else None
    if not isinstance(body, dict) or not (
        kind == "grade" or (kind == "write" and quiz.is_id(body.get("topic_id")))
    ):
        return _error("invalid_body", 'expected {kind: "write", topic_id} or {kind: "grade"}')
    name = request.path_params["subject"]
    runner: Runner = request.app.state.runner

    def start(store: SubjectStore, progress: ProgressStore) -> Response:
        topic_id, big = None, False
        if kind == "write":
            topic_id = body["topic_id"]
            leaves = quiz.scope_leaves(store, topic_id)
            bank = store.question_ids_by_topic()
            if all(len(bank.get(leaf, ())) >= TARGET_QUESTIONS for leaf in leaves):
                detail = f"every topic in scope has {TARGET_QUESTIONS} or more questions"
                return _error("nothing_to_write", detail)
            big = leaves != [topic_id]
        elif not quiz.pending_reviews(store, progress)["awaiting"]:
            return _error("nothing_to_grade", "no written answer is awaiting review")
        try:
            run = runner.start(kind, name, topic_id, build_prompt(kind, name, topic_id, big))
        except RunError as e:
            return _error(e.reason, e.detail, e.status)
        return JSONResponse(run, status_code=202)

    # Off the event loop: SQL and a fork/exec, while the same loop may serve /mcp.
    return await run_in_threadpool(_quiz, request, start)


async def _stop_run(request: Request) -> Response:
    body = await _page_json(request, "a run is stopped from this page only")
    if isinstance(body, Response):
        return body
    # Stopping waits for the process to exit; off the event loop, which may be serving the
    # run's own /mcp calls under `groundly start`.
    return JSONResponse({"run": await run_in_threadpool(request.app.state.runner.stop)})


# The names the page answers to; the Host check against DNS rebinding. Fixed, never configurable.
HOSTS = ["127.0.0.1", "localhost", "groundly.localhost"]


def create_app(mcp_app: Starlette | None = None, mcp_url: str | None = None) -> Starlette:
    """The page; with `mcp_app` (FastMCP's HTTP app, routed at `/mcp`) mounted after it, and
    `mcp_url` its address, which a page run uses instead of starting its own Groundly."""
    runner = Runner(mcp_url)
    atexit.register(runner.shutdown)  # a forced exit (a second Ctrl-C) skips the lifespan

    @asynccontextmanager
    async def lifespan(app: Starlette):
        try:
            if mcp_app is None:
                yield
            else:  # the mounted app's session manager runs in its lifespan
                async with mcp_app.router.lifespan_context(app):
                    yield
        finally:
            runner.shutdown()  # no `claude` outlives the page

    app = Starlette(
        routes=[
            Route("/", _html),
            Mount("/static", StaticFiles(directory=_WEB / "static")),
            Route("/s/{subject}", _subject_page),
            Route("/s/{subject}/test", _subject_page),
            Route("/s/{subject}/attempts/{id:int}", _subject_page),
            Route("/api/subjects", _subjects),
            Route("/api/subjects/{subject}/tree", _tree),
            Route("/api/subjects/{subject}/test", _draw),
            Route("/api/subjects/{subject}/attempts", _submit, methods=["POST"]),
            Route("/api/subjects/{subject}/attempts", _history),
            Route("/api/subjects/{subject}/attempts/{id:int}", _result),
            Route("/s/{subject}/materials/{id:int}", _material),
            Route("/api/runs", _runs),
            Route("/api/subjects/{subject}/runs", _start_run, methods=["POST"]),
            Route("/api/runs/stop", _stop_run, methods=["POST"]),
            # A Route, not Mount(""): a catch-all mount would answer wrong-method page requests
            # with 404 instead of their 405. FastMCP's own Host/Origin guard still runs inside.
            *([Route("/mcp", mcp_app)] if mcp_app else []),
        ],
        lifespan=lifespan,
        middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=HOSTS)],
    )
    app.state.runner = runner
    return app
