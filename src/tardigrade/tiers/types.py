from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Tier(str, Enum):
    AGENT = "agent"
    EMBEDDINGS = "embeddings"
    RULES = "rules"


@dataclass
class Reply:
    """A response from one of the tiers. `meta` carries tier-specific
    breadcrumbs the UI surfaces (which model answered, similarity score,
    rule that matched, MCP tools invoked)."""

    text: str
    tier: Tier
    latency_ms: float
    meta: dict[str, Any] = field(default_factory=dict)
