"""Guardrail policies applied at the app layer. In production these would
move to TrueFoundry Guardrails (input/output policies attached to the AI
Gateway and the MCP Gateway respectively) so they enforce centrally and
generate audit trail. We implement them client-side here because the demo
runs against a TF account without a Guardrails subscription configured —
the API surface is the same shape regardless.

Three policies:

  - input_guardrail(text): detects + blocks prompt-injection patterns
    BEFORE the message reaches the LLM. Returns the safe text or a refusal.
  - redact_pii(text): masks emails, US phone numbers, and 13–16-digit card
    numbers. Applied to both input (logs) and output (response).
  - validate_tool_args(tool_name, args): blocks refund tool calls whose
    amount exceeds the per-call ceiling. Returns the safe args dict or
    raises GuardrailBlock so the agent loop can return a refusal."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

REFUND_AMOUNT_CEILING_USD = 50.0  # demo ceiling — prod tier-1 is $500, tier-2 needs supervisor

_PROMPT_INJECTION_PATTERNS = [
    re.compile(r"\bignore\s+(?:(?:all|any|the|previous|prior|earlier|above|preceding)\s+)*(?:instructions|prompts|rules|guidelines|directives)\b", re.I),
    re.compile(r"\b(?:disregard|override|forget)\s+(?:(?:the|your|all|prior|above|previous)\s+)*(?:system|prompt|instructions|rules)\b", re.I),
    re.compile(r"\byou are now (?:a |an )?(?:dan|jailbroken|unrestricted|developer mode)\b", re.I),
    re.compile(r"```\s*system\s*", re.I),
    re.compile(r"\bact as (?:a |an )?(?:developer|root|admin)(?:\s+(?:mode|user))?\b", re.I),
    re.compile(r"\breveal\s+(?:the|your)\s+(?:system\s+)?(?:prompt|instructions)\b", re.I),
]

# Order matters: card numbers before phone (16 digits would otherwise be phone-y).
_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:\d[ -]?){13,16}\b"), "[CARD]"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[EMAIL]"),
    (re.compile(r"\b(?:\+?1[-.\s]?)?\(?[2-9]\d{2}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"), "[PHONE]"),
]


@dataclass
class GuardrailDecision:
    """Result of running input_guardrail on a user message."""
    safe: bool
    text: str           # original on safe=True, refusal copy on safe=False
    matched: list[str]  # which patterns triggered (empty when safe)


class GuardrailBlock(Exception):
    """Raised when a tool-arg validation fails. The agent loop catches this
    and returns a polite refusal to the user instead of executing the tool."""
    def __init__(self, tool: str, reason: str) -> None:
        self.tool = tool
        self.reason = reason
        super().__init__(f"guardrail blocked tool {tool!r}: {reason}")


def input_guardrail(text: str) -> GuardrailDecision:
    matched = [p.pattern for p in _PROMPT_INJECTION_PATTERNS if p.search(text)]
    if not matched:
        return GuardrailDecision(safe=True, text=text, matched=[])
    return GuardrailDecision(
        safe=False,
        text=(
            "I can't process that request — it looks like an attempt to "
            "override my instructions. If you have a real order or account "
            "question, please rephrase and I'll be glad to help."
        ),
        matched=matched,
    )


def redact_pii(text: str) -> str:
    out = text
    for pattern, replacement in _PII_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def validate_tool_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Returns args unchanged when safe. Raises GuardrailBlock when policy
    violated. Currently enforces the refund amount ceiling."""
    if tool_name == "initiate_refund":
        amount = args.get("amount_usd", 0)
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            raise GuardrailBlock(tool_name, f"amount_usd not numeric: {amount!r}")
        if amount > REFUND_AMOUNT_CEILING_USD:
            raise GuardrailBlock(
                tool_name,
                f"amount_usd ${amount:.2f} exceeds per-call ceiling ${REFUND_AMOUNT_CEILING_USD:.2f}; "
                "human supervisor approval required.",
            )
    return args
