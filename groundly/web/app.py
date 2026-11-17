"""The topic-tree and test page server: loopback, one process for every subject.

Titles, summaries, questions and chunk text come from stores that an imported bundle may have
written. The page writes one thing, a submitted test attempt to the student's `progress.db`,
through `agents/quiz.py`, and only from a JSON POST carrying the page's own Origin; it never
writes store.db and loads no model; under `groundly start` the same process also serves
the MCP tools at `/mcp`, whose first search loads the models. A stored filename is never trusted as a path: a material is
addressed by id and its file must resolve inside the subject's materials dir.
"""

import json
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path

from starlette.applications import Starlette
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

_WEB = Path(__file__).parent
_PAGE = (_WEB / "page.html").read_text(encoding="utf-8")

# HTML responses only: the browser's own PDF viewer needs the PDF response left unrestricted.
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def _open_subject(name: str) -> Subject | None:
    try:
        subject = Subject(name)
    except ValueError:
        return None
    return subject if subject.exists() else None


def _read(subject: Subject, reader: Callable[[SubjectStore], Response | dict | None]):
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


def _quiz(request: Request, call: Callable[[SubjectStore, ProgressStore], Response]) -> Response:
    """Run a quiz call on the subject's two stores, mapping each failure to its own answer."""
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


async def _submit(request: Request) -> Response:
    origin = request.headers.get("origin")
    if origin != f"{request.url.scheme}://{request.headers.get('host')}":
        return _error("forbidden", "a test is submitted from this page only", 403)
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        return _error("unsupported", "the body is JSON", 415)
    raw = await _read_body(request)
    if raw is None:
        return _error("too_large", "the body is larger than a test can be", 413)
    try:
        body = json.loads(raw)
    except (ValueError, RecursionError):
        return _error("invalid_body", "the body is not valid JSON")
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

    subject = _open_subject(request.path_params["subject"])
    return _quiz(request, read)


# The names the page answers to; the Host check against DNS rebinding. Fixed, never configurable.
HOSTS = ["127.0.0.1", "localhost", "groundly.localhost"]


def create_app(mcp_app: Starlette | None = None) -> Starlette:
    """The page; with `mcp_app` (FastMCP's HTTP app, routed at `/mcp`) mounted after it."""
    return Starlette(
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
            # A Route, not Mount(""): a catch-all mount would answer wrong-method page requests
            # with 404 instead of their 405. FastMCP's own Host/Origin guard still runs inside.
            *([Route("/mcp", mcp_app)] if mcp_app else []),
        ],
        # The mounted app's session manager runs in its lifespan; the parent must run it.
        lifespan=mcp_app.lifespan if mcp_app else None,
        middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=HOSTS)],
    )
