"""Private MCP tool surface for a headless first map of one indexed subject."""

from importlib.metadata import version

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware

from groundly.mcp import server


class PageMapMiddleware(Middleware):
    def __init__(self, subject: str) -> None:
        self.subject = subject

    async def on_call_tool(self, context, call_next):
        name = context.message.name
        args = context.message.arguments or {}
        if name == "get_page":
            uri = args.get("uri")
            subject = server.parse_citation_uri(uri)[0] if isinstance(uri, str) else None
        elif name in {"outline", "search", "submit_tree"}:
            subject = args.get("subject")
        else:
            raise ToolError("this tool is unavailable for a page map")
        if subject != self.subject:
            raise ToolError("a page map can only access the selected subject")
        if name == "submit_tree":
            message = context.message.model_copy(
                update={"arguments": {**args, "if_unmapped": True}}
            )
            context = context.copy(message=message)
        return await call_next(context)


def create_page_map_server(subject: str) -> FastMCP:
    scoped = FastMCP(
        "groundly", instructions=server.SERVER_INSTRUCTIONS, version=version("groundly")
    )
    for tool in (server.outline, server.get_page, server.search, server.submit_tree):
        scoped.tool(tool)
    scoped.add_middleware(PageMapMiddleware(subject))
    return scoped
