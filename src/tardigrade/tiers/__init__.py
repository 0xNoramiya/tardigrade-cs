"""The three response tiers. Each tier exposes an `answer(message, history) -> Reply`
coroutine. Higher tiers can fail; the waterfall in `tardigrade.app` falls through.
Tier 3 never fails."""

from tardigrade.tiers.types import Reply, Tier

__all__ = ["Reply", "Tier"]
