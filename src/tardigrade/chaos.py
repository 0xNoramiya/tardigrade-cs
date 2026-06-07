"""Chaos engine. Two mechanisms:

1. Model-name swap at the agent transport — every chaos scenario maps the
   primary Virtual Model name to a pre-broken Virtual Model whose priority-0
   target is configured with an invalid API key. TF's real fallback fires.
2. Per-tier disable flags — the waterfall reads `disabled_tiers()` and skips
   any tier listed. Used to demo the cascade end-to-end (kill tier 1, kill
   tier 2, prove tier 3 still answers).

State is persisted at /tmp/tardigrade-chaos.json so the FastAPI worker, CLI,
and any helper processes share the same view.

The production guardrail `TARDIGRADE_DISABLE_CHAOS=1` short-circuits both
mechanisms — load() returns an empty state regardless of the file."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from tardigrade.tiers.types import Tier

STATE_FILE = Path(os.environ.get("TARDIGRADE_CHAOS_FILE", "/tmp/tardigrade-chaos.json"))

# scenario_key -> chaos virtual-model name to substitute in place of the
# primary Virtual Model. The chaos VMs are defined in gateway-config/ with
# deliberately broken priority-0 targets so real fallback exercises.
MODEL_SCENARIOS: dict[str, str] = {
    "primary-down": "tardigrade-chaos-primary/tardigrade-chaos-primary",
    "rate-limit": "tardigrade-chaos-ratelimit/tardigrade-chaos-ratelimit",
    "all-providers-down": "tardigrade-chaos-cascade/tardigrade-chaos-cascade",
}

TIER_DISABLE_SCENARIOS: dict[str, list[Tier]] = {
    "no-agent": [Tier.AGENT],
    "no-embeddings": [Tier.EMBEDDINGS],
    "no-agent-no-embeddings": [Tier.AGENT, Tier.EMBEDDINGS],
}

# Tool-layer chaos — agent tier loses access to MCP tools but the LLM still
# runs. Demonstrates graceful within-tier degradation (no order lookup, but
# the agent can still answer policy questions from prompt context).
TOOL_SCENARIOS: set[str] = {"tools-down", "bad-output"}

# Latency-class chaos — agent tier sleeps before calling the gateway. With
# the configured timeout (20s in agent.py), 25s of injected sleep triggers a
# real httpx ReadTimeout, the agent tier raises, and the waterfall cascades
# to tier 2. Tunable via TARDIGRADE_SLOW_DELAY_SECONDS env var (default 25).
LATENCY_SCENARIOS: dict[str, float] = {
    "slow-response": float(os.environ.get("TARDIGRADE_SLOW_DELAY_SECONDS", "25")),
}

ALL_SCENARIOS = sorted(
    set(MODEL_SCENARIOS)
    | set(TIER_DISABLE_SCENARIOS)
    | TOOL_SCENARIOS
    | set(LATENCY_SCENARIOS)
)


def _hard_disabled() -> bool:
    return os.environ.get("TARDIGRADE_DISABLE_CHAOS", "0").lower() in {"1", "true", "yes"}


@dataclass
class ChaosState:
    scenario: str = ""
    # tracked separately so the model-swap and tier-disable can compose
    disabled_tiers: list[str] = field(default_factory=list)

    @classmethod
    def load(cls) -> "ChaosState":
        if _hard_disabled() or not STATE_FILE.exists():
            return cls()
        try:
            data = json.loads(STATE_FILE.read_text())
            return cls(
                scenario=data.get("scenario", ""),
                disabled_tiers=data.get("disabled_tiers", []),
            )
        except (OSError, json.JSONDecodeError):
            return cls()

    def save(self) -> None:
        STATE_FILE.write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def clear(cls) -> None:
        if STATE_FILE.exists():
            STATE_FILE.unlink()


def activate(scenario: str) -> ChaosState:
    """Turn on a chaos scenario by name. Composes model-swap and tier-disable."""
    if scenario not in ALL_SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}. options: {ALL_SCENARIOS}")
    state = ChaosState.load()
    if (scenario in MODEL_SCENARIOS or scenario in TOOL_SCENARIOS
            or scenario in LATENCY_SCENARIOS):
        state.scenario = scenario
    if scenario in TIER_DISABLE_SCENARIOS:
        state.disabled_tiers = [t.value for t in TIER_DISABLE_SCENARIOS[scenario]]
    state.save()
    return state


def current_latency_inject() -> float:
    """Returns seconds of artificial delay to inject before the agent's
    gateway call, or 0 when no latency-class scenario is active.
    The production guardrail short-circuits this via ChaosState.load()."""
    state = ChaosState.load()
    if state.scenario in LATENCY_SCENARIOS:
        return LATENCY_SCENARIOS[state.scenario]
    return 0.0


def current_bad_output_active() -> bool:
    return ChaosState.load().scenario == "bad-output"


def clear() -> None:
    ChaosState.clear()


def current_model_swap(requested_model: str) -> tuple[str, str | None]:
    """Returns (effective_model, chaos_scenario_or_None). Called by the agent
    tier just before the gateway request."""
    state = ChaosState.load()
    if state.scenario and state.scenario in MODEL_SCENARIOS:
        return MODEL_SCENARIOS[state.scenario], state.scenario
    return requested_model, None


def disabled_tiers() -> set[Tier]:
    state = ChaosState.load()
    return {Tier(t) for t in state.disabled_tiers if t in Tier._value2member_map_}
