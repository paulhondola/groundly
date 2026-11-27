"""Writes what an MCP session is doing to activity.db: its start, each tool call's start and end,
and the gates' acceptances. Registered by the verbs that serve MCP (`groundly mcp`, `start`), never
at import of the server, so tests and the bare page log nothing. Arguments and results stay out of
the log except the counts and ids named below: no queries, no course text."""

from fastmcp.server.middleware import Middleware

from groundly.core import activity
from groundly.core.paths import validate_subject_name
from groundly.mcp.forward import FIRST_MAP_FORWARDED, FORWARD_HEADER, PAGE_RUN_HEADER


def _accepted(tool: str, args: dict, result: dict) -> tuple[str, dict] | None:
    if tool == "submit_tree" and result.get("accepted") is True:
        tree = args.get("tree") or []
        subtopics = sum(len(t.get("subtopics") or []) for t in tree if isinstance(t, dict))
        return "tree_accepted", {"topics": len(tree), "subtopics": subtopics}
    if tool == "submit_questions" and result.get("accepted"):
        return "questions_accepted", {
            "topic_id": args.get("topic_id"),
            "count": len(result["accepted"]),
        }
    if tool == "review_answers" and result.get("recorded"):
        return "answers_reviewed", {"count": len(result["recorded"])}
    return None


_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
# A page run's `claude` reports the same client name as the student's own sessions, and
# 2026-07-28 requests carry no session, so its MCP config adds this header (web/runs.py).
# Display only: any local client could send it.
PAGE_RUN_CLIENT = "Groundly page run"
# The last handshake-era revision: up to it a connection keeps one session id for its life
# (Codex); from 2026-07-28 every request gets a fresh one (Claude Code), which names nothing.
_LAST_HANDSHAKE_ERA = "2025-11-25"


def _name(info) -> str | None:
    """The host's self-reported name, capped: it is free text from the client."""
    name = info.get("name") if isinstance(info, dict) else getattr(info, "name", None)
    return name[:64] if isinstance(name, str) else None


def _subject(args: dict) -> str | None:
    """The `subject` argument only when it is a valid subject name: a host may pass anything."""
    subject = args.get("subject")
    if not isinstance(subject, str):
        return None
    try:
        validate_subject_name(subject)
    except ValueError:
        return None
    return subject


def _request_has_header(context, header: str) -> bool:
    ctx = context.fastmcp_context
    rc = ctx.request_context if ctx else None
    request = getattr(rc, "request", None)
    return request is not None and header in request.headers


class ActivityMiddleware(Middleware):
    def __init__(self, source: str, *, page_run: bool = False) -> None:
        self.source = source
        self.page_run = page_run

    def _log(self, event: str, context, *, client: str | None = None, **kw) -> None:
        """A 2026-07-28 request names its client in `_meta` and has no session; a handshake-era
        one names it once, kept on its session's client params, and keeps its session id."""
        ctx = context.fastmcp_context
        rc = ctx.request_context if ctx else None
        session = None
        if rc is not None:
            info = (rc.meta or {}).get(_CLIENT_INFO)
            if info is None:
                info = getattr(getattr(rc.session, "client_params", None), "client_info", None)
            client = client or _name(info)
            if self.source == "mcp_http":
                if rc.protocol_version <= _LAST_HANDSHAKE_ERA:
                    session = ctx.session_id
                request = getattr(rc, "request", None)
                if request is not None and PAGE_RUN_HEADER in request.headers:
                    client = PAGE_RUN_CLIENT
        activity.record(
            self.source,
            event,
            client=PAGE_RUN_CLIENT if self.page_run else client,
            session=session,
            **kw,
        )

    async def on_initialize(self, context, call_next):
        if _request_has_header(context, FORWARD_HEADER):
            return await call_next(context)
        info = getattr(getattr(context.message, "params", None), "client_info", None)
        self._log("session_start", context, client=_name(info))
        return await call_next(context)

    async def on_discover(self, context, call_next):
        if _request_has_header(context, FORWARD_HEADER):
            return await call_next(context)
        self._log("session_start", context)
        return await call_next(context)

    async def on_call_tool(self, context, call_next):
        name = context.message.name
        args = context.message.arguments or {}
        subject = _subject(args)
        if _request_has_header(context, FORWARD_HEADER):
            result = await call_next(context)
            # The parent may have stopped its CLI while this locked write was still finishing.
            if (
                name == "submit_tree"
                and args.get("if_unmapped") is True
                and _request_has_header(context, PAGE_RUN_HEADER)
            ):
                accepted = _accepted(name, args, result.structured_content or {})
                if accepted:
                    self._log(
                        accepted[0],
                        context,
                        client=PAGE_RUN_CLIENT,
                        subject=subject,
                        detail=accepted[1],
                    )
            return result
        tool = name if name in activity.TOOL_ORB else "unknown"
        self._log("tool_call", context, subject=subject, detail={"tool": tool})
        try:
            result = await call_next(context)
        finally:
            self._log("tool_end", context, subject=subject, detail={"tool": tool})
        accepted = _accepted(name, args, result.structured_content or {})
        ctx = context.fastmcp_context
        forwarded_first_map = (
            accepted
            and name == "submit_tree"
            and ctx is not None
            and await ctx.get_state(FIRST_MAP_FORWARDED)
        )
        if accepted and not forwarded_first_map:
            self._log(accepted[0], context, subject=subject, detail=accepted[1])
        return result
