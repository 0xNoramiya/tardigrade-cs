"""Tier 1 — the LLM agent. Routes through TrueFoundry AI Gateway using the
standard OpenAI SDK with a TF base_url. Tool calls are dispatched via the
TF MCP Gateway client (see `tardigrade.mcp_client`).

The chaos engine intercepts the model name just before the request — when
a chaos scenario is active, the requested model is swapped for a pre-broken
Virtual Model so TF's real fallback chain exercises end-to-end."""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Any

import httpx
from openai import AsyncOpenAI

from tardigrade.chaos import current_model_swap
from tardigrade.config import get_settings
from tardigrade.guardrails import GuardrailBlock, validate_tool_args
from tardigrade.mcp_client import call_tool, list_tools
from tardigrade.tiers.types import Reply, Tier

SYSTEM_PROMPT = (
    "You are TardigradeCS, a customer-service agent for an e-commerce store. "
    "Help with returns, refunds, shipping, order changes, and account questions. "
    "Use the available tools (order_lookup, initiate_refund, track_shipment) "
    "when the customer references a specific order. Be concise (≤3 sentences) "
    "and confirm any action before taking it. If a tool fails, fall back to "
    "asking the customer for missing details rather than apologizing."
)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        settings = get_settings()
        if not (settings.tfy_api_key and settings.tfy_gateway_base_url):
            raise RuntimeError(
                "TFY_API_KEY and TFY_GATEWAY_BASE_URL must be set to use the agent tier."
            )
        _client = AsyncOpenAI(
            api_key=settings.tfy_api_key,
            base_url=settings.tfy_gateway_base_url,
            timeout=httpx.Timeout(20.0, connect=5.0),
            max_retries=0,  # let TF gateway handle retries+fallback
        )
    return _client


async def answer(message: str, history: Sequence[dict[str, str]] | None = None) -> Reply:
    """Run a tool-using chat turn through TF Gateway. Raises on any failure
    so the waterfall in app.py can catch and fall through to tier 2."""
    start = time.perf_counter()
    settings = get_settings()
    client = _get_client()

    requested_model = settings.tardigrade_primary_model
    effective_model, chaos_scenario = current_model_swap(requested_model)

    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(dict(h) for h in history)
    messages.append({"role": "user", "content": message})

    tools = await list_tools()
    tool_calls_made: list[dict[str, Any]] = []

    for _ in range(4):  # at most 4 tool-call hops
        resp = await client.chat.completions.create(
            model=effective_model,
            messages=messages,
            tools=tools or None,
            tool_choice="auto" if tools else None,
            temperature=0.2,
        )
        choice = resp.choices[0]
        msg = choice.message

        if not msg.tool_calls:
            text = msg.content or ""
            return Reply(
                text=text,
                tier=Tier.AGENT,
                latency_ms=(time.perf_counter() - start) * 1000,
                meta={
                    "requested_model": requested_model,
                    "effective_model": effective_model,
                    "resolved_model": resp.model,
                    "chaos_scenario": chaos_scenario,
                    "tool_calls": tool_calls_made,
                    "usage": {
                        "prompt_tokens": getattr(resp.usage, "prompt_tokens", 0),
                        "completion_tokens": getattr(resp.usage, "completion_tokens", 0),
                    },
                },
            )

        # Tool-calling roundtrip
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ],
        })
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments or "{}")
            try:
                args = validate_tool_args(tc.function.name, args)
                result = await call_tool(tc.function.name, args)
                tool_calls_made.append({"name": tc.function.name, "args": args, "result": result[:120]})
            except GuardrailBlock as gb:
                result = f"GUARDRAIL_BLOCKED: {gb.reason}"
                tool_calls_made.append({"name": tc.function.name, "args": args,
                                         "result": result, "guardrail_blocked": True})
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

    raise RuntimeError("agent exceeded tool-call hop limit (4)")
