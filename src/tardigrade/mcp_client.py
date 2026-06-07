"""MCP client. Two transports, chosen by config:

  - Remote: TF MCP Gateway Streamable-HTTP (production path).
    Set TFY_MCP_GATEWAY_URL and TFY_API_KEY → tool calls go via the gateway,
    which adds auth, audit trail, and Guardrail policies on top.
  - Local: stdio subprocess running `python -m tardigrade.mcp_server`.
    Used when no gateway URL is configured (dev + demo). The same tools, no
    deployment dance.

A chaos scenario `tools-down` makes `list_tools()` return [] regardless of
transport — the agent tier then answers without tool access, demonstrating
graceful degradation inside tier 1 before the waterfall kicks in.

Session lifecycle: we open and tear down a fresh MCP session per call. The
stdio transport in the `mcp` package uses anyio cancel scopes that can't be
shared across asyncio tasks (e.g. distinct FastAPI requests), so caching the
session would crash on the second concurrent call. The tool-list IS cached
because it's just JSON."""

from __future__ import annotations

import asyncio
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, AsyncIterator

from mcp import ClientSession, StdioServerParameters

from tardigrade.config import get_settings

_tools_cache: list[dict[str, Any]] | None = None
_tools_cache_lock = asyncio.Lock()


def _tools_chaos_active() -> bool:
    from tardigrade.chaos import ChaosState
    return ChaosState.load().scenario == "tools-down"


@asynccontextmanager
async def _session() -> AsyncIterator[ClientSession | None]:
    """Open a fresh MCP session, yield it, tear it down. Yields None when no
    transport can be opened (caller should treat as 'no tools available')."""
    settings = get_settings()
    stack = AsyncExitStack()
    try:
        await stack.__aenter__()
        if settings.tfy_mcp_gateway_url:
            from mcp.client.streamable_http import streamablehttp_client
            headers = ({"Authorization": f"Bearer {settings.tfy_api_key}"}
                       if settings.tfy_api_key else None)
            read, write, _ = await stack.enter_async_context(
                streamablehttp_client(settings.tfy_mcp_gateway_url, headers=headers)
            )
        else:
            from mcp.client.stdio import stdio_client
            params = StdioServerParameters(
                command=sys.executable, args=["-m", "tardigrade.mcp_server"], env=None,
            )
            read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        yield session
    except Exception:
        yield None
    finally:
        try:
            await stack.aclose()
        except Exception:
            # stdio teardown can race; ignore — process is already exiting or
            # the client connection is already torn down.
            pass


async def list_tools() -> list[dict[str, Any]]:
    """Tool specs in OpenAI format. Empty when chaos active or no transport."""
    if _tools_chaos_active():
        return []
    global _tools_cache
    if _tools_cache is not None:
        return _tools_cache
    async with _tools_cache_lock:
        if _tools_cache is not None:
            return _tools_cache
        async with _session() as session:
            if session is None:
                return []
            try:
                result = await session.list_tools()
            except Exception:
                return []
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
    if _tools_chaos_active():
        return f"(tool {name!r} unavailable — MCP Gateway returned 503)"
    async with _session() as session:
        if session is None:
            return f"(tool {name!r} unavailable — MCP transport not reachable)"
        try:
            result = await session.call_tool(name, arguments=arguments)
        except Exception as e:
            return f"(tool {name!r} failed: {type(e).__name__}: {e})"
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
