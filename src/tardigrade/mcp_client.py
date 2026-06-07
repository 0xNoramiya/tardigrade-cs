"""Thin client for the TrueFoundry MCP Gateway (Streamable-HTTP transport).

Lazily opens a session on first call_tool/list_tools and reuses it. If the
gateway is unreachable, `list_tools()` returns [] and `call_tool` raises so
the agent can degrade to no-tool mode (which often still answers usefully)."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession

from tardigrade.config import get_settings

_session: ClientSession | None = None
_stack: AsyncExitStack | None = None
_lock = asyncio.Lock()
_tools_cache: list[dict[str, Any]] | None = None


async def _open_session() -> ClientSession | None:
    """Open and cache a session against TF MCP Gateway. Returns None if no
    gateway URL is configured (agent runs without tools)."""
    global _session, _stack
    settings = get_settings()
    if not settings.tfy_mcp_gateway_url:
        return None
    if _session is not None:
        return _session
    from mcp.client.streamable_http import streamablehttp_client

    _stack = AsyncExitStack()
    await _stack.__aenter__()
    headers = {"Authorization": f"Bearer {settings.tfy_api_key}"} if settings.tfy_api_key else None
    read, write, _ = await _stack.enter_async_context(
        streamablehttp_client(settings.tfy_mcp_gateway_url, headers=headers)
    )
    session = await _stack.enter_async_context(ClientSession(read, write))
    await session.initialize()
    _session = session
    return _session


async def list_tools() -> list[dict[str, Any]]:
    """Returns tool specs in OpenAI tool-calling format. Empty list if no
    MCP Gateway is reachable — the agent can still answer without tools."""
    global _tools_cache
    if _tools_cache is not None:
        return _tools_cache
    async with _lock:
        if _tools_cache is not None:
            return _tools_cache
        try:
            session = await _open_session()
        except Exception:
            _tools_cache = []
            return _tools_cache
        if session is None:
            _tools_cache = []
            return _tools_cache
        try:
            result = await session.list_tools()
        except Exception:
            _tools_cache = []
            return _tools_cache
        _tools_cache = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description or "",
                    "parameters": t.inputSchema or {"type": "object", "properties": {}},
                },
            }
            for t in result.tools
        ]
        return _tools_cache


async def call_tool(name: str, arguments: dict[str, Any]) -> str:
    session = await _open_session()
    if session is None:
        return f"(tool {name!r} unavailable — MCP gateway not configured)"
    result = await session.call_tool(name, arguments=arguments)
    parts = getattr(result, "content", None) or []
    out: list[str] = []
    for p in parts:
        text = getattr(p, "text", None)
        if text:
            out.append(text)
    return "\n".join(out) or str(result)


def invalidate_tools_cache() -> None:
    global _tools_cache
    _tools_cache = None
