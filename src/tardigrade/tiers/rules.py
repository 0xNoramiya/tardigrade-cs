"""Tier 3 — rule-based floor. NEVER raises, ALWAYS returns. This is the SLA
guarantee: every customer message gets a response.

Each rule is a (compiled regex, intent name, response template) triple.
Templates can reference {ticket_id} which is auto-generated per call. The
matcher is greedy-first — order matters."""

from __future__ import annotations

import re
import time
from collections.abc import Sequence

from tardigrade.tiers.types import Reply, Tier

_TICKET_PREFIX = "TKT"


def _ticket_id() -> str:
    return f"{_TICKET_PREFIX}-{int(time.time() * 1000) % 10_000_000}"


# Order matters — most specific first.
RULES: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(r"\b(refund|money back|reimburse)\b", re.I),
        "refund_request",
        "I've opened refund request {ticket_id}. Refunds to the original payment "
        "method clear in 5–7 business days. You'll receive an email confirmation "
        "within the hour.",
    ),
    (
        re.compile(r"\b(track|where is|status of|when will).{0,30}(order|package|shipment|delivery)\b", re.I),
        "order_status",
        "I've logged your shipment status request as {ticket_id}. Our agents will "
        "reply with a tracking update within 2 hours. Please have your order "
        "number ready.",
    ),
    (
        re.compile(r"\b(return label|send.{0,10}label|return.{0,20}item)\b", re.I),
        "return_label",
        "Return label request {ticket_id} created. A prepaid label will arrive at "
        "your email within 30 minutes. Pack the item in original packaging and "
        "drop off at any carrier location.",
    ),
    (
        re.compile(r"\b(cancel|stop|don't ship).{0,20}(order|purchase)\b", re.I),
        "cancel_order",
        "Cancellation request {ticket_id} submitted. If your order hasn't shipped, "
        "it will be cancelled and refunded within 24 hours. If it has shipped, "
        "use the return process once it arrives.",
    ),
    (
        re.compile(r"\b(damaged|broken|defect|wrong item|missing)\b", re.I),
        "damaged_or_wrong",
        "I'm sorry about that. Issue {ticket_id} is now logged as a priority "
        "case. A specialist will email you within 4 hours with a replacement "
        "or refund.",
    ),
    (
        re.compile(r"\b(speak|talk|chat).{0,15}(human|agent|person|representative)\b", re.I),
        "contact_human",
        "I've escalated to a human agent — ticket {ticket_id}. Expected response "
        "time: 15 minutes during business hours, 2 hours otherwise.",
    ),
    (
        re.compile(r"\b(hi|hello|hey|good (morning|afternoon|evening))\b", re.I),
        "greeting",
        "Hi! I can help with returns, refunds, shipping, and order changes. "
        "What's going on with your order?",
    ),
    (
        re.compile(r"\b(thank|thanks|appreciate)\b", re.I),
        "thanks",
        "You're welcome. If anything else comes up, just message again.",
    ),
]


async def answer(message: str, history: Sequence[dict[str, str]] | None = None) -> Reply:
    start = time.perf_counter()
    tid = _ticket_id()
    for pattern, intent, template in RULES:
        if pattern.search(message):
            return Reply(
                text=template.format(ticket_id=tid),
                tier=Tier.RULES,
                latency_ms=(time.perf_counter() - start) * 1000,
                meta={"intent": intent, "ticket_id": tid, "rule": pattern.pattern},
            )
    # Catch-all — never let a customer walk away with nothing.
    return Reply(
        text=(
            f"Thanks for reaching out — I've opened case {tid} and a member of "
            "our team will reply by email within 4 hours. If this is urgent, "
            "reply with 'speak to a human' for an immediate escalation."
        ),
        tier=Tier.RULES,
        latency_ms=(time.perf_counter() - start) * 1000,
        meta={"intent": "catch_all", "ticket_id": tid},
    )
