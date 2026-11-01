"""The topic-tree page server: read-only, loopback, one process for every subject.

Titles, summaries and chunk text come from stores that an imported bundle may have written, so
nothing here is writable, no model loads, and a stored filename is never trusted as a path:
a material is addressed by id and its file must resolve inside the subject's materials dir.
"""

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

from groundly.core.paths import discover_subjects
from groundly.core.store import SubjectStore
from groundly.core.subject import Subject

_WEB = Path(__file__).parent
_PAGE = (_WEB / "page.html").read_text(encoding="utf-8")

# HTML responses only: the browser's own PDF viewer needs the PDF response left unrestricted.
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
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
        entry: dict = {"subject": name, "has_tree": False}
        try:
            entry["has_tree"] = bool(SubjectStore(subject.store_db_path).topic_ids())
        except (sqlite3.DatabaseError, RuntimeError) as e:
            entry["error"] = str(e)
        out.append(entry)
    return JSONResponse(out)


def _stored_pdf(subject: Subject, filename) -> Path | None:
    """The stored PDF a material's filename names, or None. The name is untrusted (an
    imported bundle wrote it), so the resolved path must stay inside the materials dir."""
    root = subject.materials_dir.resolve()
    try:
        path = (root / filename).resolve()
        servable = path.is_relative_to(root) and path.suffix.lower() == ".pdf" and path.is_file()
    except (ValueError, OSError, TypeError):  # NUL in a name; a non-text name from a hostile db
        return None
    return path if servable else None


def _tree(request: Request) -> Response:
    subject = _open_subject(request.path_params["subject"])
    if subject is None:
        name = request.path_params["subject"]
        return JSONResponse({"error": f"unknown subject {name!r}"}, status_code=404)
    result = _read(subject, lambda store: {"tree": store.tree()})
    if isinstance(result, Response):
        return result
    # `pdf` tells the page whether a link would open: a bundle exported without its
    # materials still cites PDFs it cannot serve.
    openable: dict[int, bool] = {}

    def flag(nodes: list[dict]) -> None:
        for node in nodes:
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


def create_app() -> Starlette:
    return Starlette(
        routes=[
            Route("/", _html),
            Mount("/static", StaticFiles(directory=_WEB / "static")),
            Route("/s/{subject}", _subject_page),
            Route("/api/subjects", _subjects),
            Route("/api/subjects/{subject}/tree", _tree),
            Route("/s/{subject}/materials/{id:int}", _material),
        ],
        middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])],
    )
