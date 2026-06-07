"""FastAPI app: serves the chat UI, runs the 3-tier waterfall, exposes the
chaos panel. Tier 3 is the SLA floor — even when 1 and 2 are disabled or
broken, /chat always returns a real reply."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from tardigrade import chaos as chaos_engine
from tardigrade.guardrails import input_guardrail, redact_pii
from tardigrade.tiers import Tier
from tardigrade.tiers import agent, embeddings, rules
from tardigrade.tiers.types import Reply

log = logging.getLogger("tardigrade")

app = FastAPI(title="TardigradeCS", version="0.1.0")

_WEB_DIR = Path(__file__).resolve().parents[2] / "web"


class ChatRequest(BaseModel):
    message: str
    history: list[dict[str, str]] = []


class TierAttempt(BaseModel):
    tier: str
    ok: bool
    latency_ms: float
    note: str = ""


class ChatResponse(BaseModel):
    reply: str
    served_by: str
    latency_ms: float
    attempts: list[TierAttempt]
    meta: dict[str, Any]


async def _waterfall(message: str, history: list[dict[str, str]]) -> ChatResponse:
    attempts: list[TierAttempt] = []
    start = time.perf_counter()

    # --- Tier 0: Input guardrail (before any compute spend) ---
    decision = input_guardrail(message)
    if not decision.safe:
        return ChatResponse(
            reply=decision.text,
            served_by="guardrail",
            latency_ms=(time.perf_counter() - start) * 1000,
            attempts=[TierAttempt(
                tier="guardrail", ok=True,
                latency_ms=(time.perf_counter() - start) * 1000,
                note=f"blocked: {', '.join(decision.matched[:2])}",
            )],
            meta={"guardrail": "input", "matched": decision.matched},
        )

    # PII redaction on the message that goes to the LLM (real surfaces would
    # tokenize + restore; for the demo we just mask).
    message = redact_pii(message)
    disabled = chaos_engine.disabled_tiers()

    # --- Tier 1: Agent ---
    if Tier.AGENT in disabled:
        attempts.append(TierAttempt(tier=Tier.AGENT.value, ok=False, latency_ms=0.0,
                                     note="disabled by chaos"))
    else:
        try:
            reply = await agent.answer(message, history)
            return _finalize(reply, attempts, start)
        except Exception as e:  # noqa: BLE001
            log.warning("agent tier failed: %s", e)
            attempts.append(TierAttempt(
                tier=Tier.AGENT.value, ok=False,
                latency_ms=(time.perf_counter() - start) * 1000,
                note=f"{type(e).__name__}: {e}"[:200],
            ))

    # --- Tier 2: Embeddings ---
    t2_start = time.perf_counter()
    if Tier.EMBEDDINGS in disabled:
        attempts.append(TierAttempt(tier=Tier.EMBEDDINGS.value, ok=False, latency_ms=0.0,
                                     note="disabled by chaos"))
    else:
        try:
            reply = await embeddings.answer(message, history)
            if reply is not None:
                return _finalize(reply, attempts, start)
            attempts.append(TierAttempt(
                tier=Tier.EMBEDDINGS.value, ok=False,
                latency_ms=(time.perf_counter() - t2_start) * 1000,
                note="below similarity threshold",
            ))
        except Exception as e:  # noqa: BLE001
            log.warning("embeddings tier failed: %s", e)
            attempts.append(TierAttempt(
                tier=Tier.EMBEDDINGS.value, ok=False,
                latency_ms=(time.perf_counter() - t2_start) * 1000,
                note=f"{type(e).__name__}: {e}"[:200],
            ))

    # --- Tier 3: Rules (never fails) ---
    reply = await rules.answer(message, history)
    return _finalize(reply, attempts, start)


def _finalize(reply: Reply, attempts: list[TierAttempt], start: float) -> ChatResponse:
    return ChatResponse(
        reply=reply.text,
        served_by=reply.tier.value,
        latency_ms=(time.perf_counter() - start) * 1000,
        attempts=attempts + [TierAttempt(
            tier=reply.tier.value, ok=True, latency_ms=reply.latency_ms,
            note="served",
        )],
        meta=reply.meta,
    )


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    return await _waterfall(req.message, req.history)


@app.get("/chaos/status")
async def chaos_status() -> dict[str, Any]:
    state = chaos_engine.ChaosState.load()
    return {
        "scenario": state.scenario,
        "disabled_tiers": state.disabled_tiers,
        "available": chaos_engine.ALL_SCENARIOS,
        "hard_disabled": chaos_engine._hard_disabled(),
    }


@app.post("/chaos/break/{scenario}")
async def chaos_break(scenario: str) -> dict[str, Any]:
    try:
        state = chaos_engine.activate(scenario)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"scenario": state.scenario, "disabled_tiers": state.disabled_tiers}


@app.post("/chaos/clear")
async def chaos_clear() -> dict[str, str]:
    chaos_engine.clear()
    return {"status": "cleared"}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(_WEB_DIR / "index.html")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
