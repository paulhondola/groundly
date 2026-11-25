"""Writes what an MCP session is doing to activity.db: its start, each tool call's start and end,
and the gates' acceptances. Registered by the verbs that serve MCP (`groundly mcp`, `start`), never
at import of the server, so tests and the bare page log nothing. Arguments and results stay out of
the log except the counts and ids named below: no queries, no course text."""

from fastmcp.server.middleware import Middleware

from groundly.core import activity


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


def _name(info) -> str | None:
    name = info.get("name") if isinstance(info, dict) else getattr(info, "name", None)
    return name if isinstance(name, str) else None


class ActivityMiddleware(Middleware):
    def __init__(self, source: str) -> None:
        self.source = source

    def _log(self, event: str, context, *, client: str | None = None, **kw) -> None:
        """A 2026-07-28 request names its client in `_meta` and gets a fresh session id each
        time; a handshake-era one names it once, kept on its session's client params."""
        ctx = context.fastmcp_context
        rc = ctx.request_context if ctx else None
        session = None
        if rc is not None:
            info = (rc.meta or {}).get(_CLIENT_INFO)
            if info is None:
                info = getattr(getattr(rc.session, "client_params", None), "client_info", None)
            client = client or _name(info)
            session = ctx.session_id if self.source == "mcp_http" else None
        activity.record(self.source, event, client=client, session=session, **kw)

    async def on_initialize(self, context, call_next):
        info = getattr(getattr(context.message, "params", None), "client_info", None)
        self._log("session_start", context, client=_name(info))
        return await call_next(context)

    async def on_discover(self, context, call_next):
        self._log("session_start", context)
        return await call_next(context)

    async def on_call_tool(self, context, call_next):
        tool = context.message.name
        args = context.message.arguments or {}
        subject = args.get("subject") if isinstance(args.get("subject"), str) else None
        self._log("tool_call", context, subject=subject, detail={"tool": tool})
        try:
            result = await call_next(context)
        finally:
            self._log("tool_end", context, subject=subject, detail={"tool": tool})
        accepted = _accepted(tool, args, result.structured_content or {})
        if accepted:
            self._log(accepted[0], context, subject=subject, detail=accepted[1])
        return result
