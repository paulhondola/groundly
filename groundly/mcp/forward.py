"""A stdio `groundly mcp` sends the three tools that need the models to a running `groundly start`
(found through start.json), so every host on this machine shares its one model load.

Anything that fails before the call is sent (no live `start`, another version, a refused
connection) serves the call locally, as without `start`. Nothing is retried locally once the call
was sent: `start` may have stored the tree or the questions already. Registered by `groundly mcp`
for stdio only; the stdio server's own ActivityMiddleware logs the call, and `start` skips
requests carrying the forward header so the call shows once."""

import asyncio
import contextlib
import logging
from typing import Any

import httpx2
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware
from fastmcp.tools.base import ToolResult

from groundly.core import startfile

FORWARDED_TOOLS = frozenset({"search", "submit_tree", "submit_questions"})
FORWARD_HEADER = "x-groundly-forwarded"
PAGE_RUN_HEADER = "x-groundly-page-run"
FIRST_MAP_FORWARDED = "groundly:first_map_forwarded"
STOPPED_MESSAGE = "groundly start stopped during the call — retry it"
# A `start` that accepts connections but never answers (stopped with Ctrl-Z) is not reachable;
# only the handshake is bounded, the call itself keeps no read timeout.
HANDSHAKE_TIMEOUT = 5.0

_log = logging.getLogger(__name__)


def _no_read_timeout(
    headers: dict[str, str] | None = None,
    timeout: httpx2.Timeout | None = None,
    auth: httpx2.Auth | None = None,
    **kwargs: Any,
) -> httpx2.AsyncClient:
    """A large `submit_tree` can stay silent for minutes; the host's own timeout still applies."""
    read_forever = httpx2.Timeout(30.0, read=None)
    return httpx2.AsyncClient(headers=headers, auth=auth, timeout=read_forever, **kwargs)


class ForwardMiddleware(Middleware):
    def __init__(self, *, page_run: bool = False) -> None:
        self.page_run = page_run

    async def on_call_tool(self, context, call_next):
        name = context.message.name
        if name not in FORWARDED_TOOLS:
            return await call_next(context)
        info = startfile.read()
        if info is None or info.version != startfile.VERSION:
            return await call_next(context)
        args = context.message.arguments or {}
        first_map = name == "submit_tree" and args.get("if_unmapped") is True
        headers = {FORWARD_HEADER: "1"}
        if first_map and self.page_run:
            headers[PAGE_RUN_HEADER] = "1"
        transport = StreamableHttpTransport(
            f"http://127.0.0.1:{info.port}/mcp",
            headers=headers,
            httpx_client_factory=_no_read_timeout,
        )
        client = Client(transport, init_timeout=HANDSHAKE_TIMEOUT)
        try:
            await client.__aenter__()
        except Exception as exc:  # noqa: BLE001 - nothing was sent: serve it here
            _log.info(
                "groundly start on port %d not reachable (%s); serving locally", info.port, exc
            )
            return await call_next(context)
        try:
            server = client.server_info
            if server is None or server.name != "groundly" or server.version != startfile.VERSION:
                return await call_next(context)
            if first_map:
                # An older process can report the same package version during development.
                try:
                    tools = await asyncio.wait_for(client.list_tools(), HANDSHAKE_TIMEOUT)
                except Exception:  # noqa: BLE001 - discovery sent no write
                    return await call_next(context)
                if not any(
                    tool.name == "submit_tree"
                    and "if_unmapped" in tool.input_schema.get("properties", {})
                    for tool in tools
                ):
                    return await call_next(context)
            try:
                result = await client.call_tool_mcp(name, args)
            except Exception as exc:
                raise ToolError(STOPPED_MESSAGE) from exc
            if first_map and self.page_run and context.fastmcp_context is not None:
                await context.fastmcp_context.set_state(
                    FIRST_MAP_FORWARDED, True, serializable=False
                )
            return ToolResult(
                content=result.content,
                structured_content=result.structured_content,
                is_error=result.is_error,
            )
        finally:
            with contextlib.suppress(Exception):
                await client.__aexit__(None, None, None)
