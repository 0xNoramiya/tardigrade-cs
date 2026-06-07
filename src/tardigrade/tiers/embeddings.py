"""Tier 2 — embedding-based FAQ retrieval. When the agent tier fails (provider
outage, gateway down, rate-limit), we encode the user message with a small
sentence-transformer and cosine-search a hand-curated FAQ corpus. If top-1
similarity is above the threshold we return that answer; otherwise we hand
off to tier 3.

Model is loaded lazily on first call so `tardigrade serve --no-embeddings`
(or a missing model on disk) doesn't block startup."""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from tardigrade.config import get_settings
from tardigrade.tiers.types import Reply, Tier

_MODEL: Any = None
_FAQ_VECTORS: np.ndarray | None = None
_FAQ_ENTRIES: list[dict[str, str]] = []


def _load_model() -> Any:
    global _MODEL
    if _MODEL is None:
        from sentence_transformers import SentenceTransformer
        settings = get_settings()
        _MODEL = SentenceTransformer(settings.tardigrade_embedding_model)
    return _MODEL


def _load_corpus() -> tuple[np.ndarray, list[dict[str, str]]]:
    global _FAQ_VECTORS, _FAQ_ENTRIES
    if _FAQ_VECTORS is not None:
        return _FAQ_VECTORS, _FAQ_ENTRIES
    settings = get_settings()
    path = Path(settings.tardigrade_faq_path)
    if not path.is_absolute():
        # Resolve relative to current working directory first, then repo root.
        cwd = Path.cwd() / path
        if cwd.exists():
            path = cwd
        else:
            path = Path(__file__).resolve().parents[3] / path
    _FAQ_ENTRIES = json.loads(path.read_text())
    model = _load_model()
    questions = [e["question"] for e in _FAQ_ENTRIES]
    vectors = model.encode(questions, normalize_embeddings=True, show_progress_bar=False)
    _FAQ_VECTORS = np.asarray(vectors, dtype=np.float32)
    return _FAQ_VECTORS, _FAQ_ENTRIES


async def answer(message: str, history: Sequence[dict[str, str]] | None = None) -> Reply | None:
    """Returns a Reply if a FAQ entry clears the similarity threshold,
    otherwise None (so the waterfall falls through to tier 3)."""
    start = time.perf_counter()
    settings = get_settings()
    vectors, entries = _load_corpus()
    model = _load_model()

    query_vec = model.encode([message], normalize_embeddings=True, show_progress_bar=False)[0]
    sims = vectors @ np.asarray(query_vec, dtype=np.float32)
    top_idx = int(np.argmax(sims))
    top_score = float(sims[top_idx])
    latency_ms = (time.perf_counter() - start) * 1000

    if top_score < settings.tardigrade_embedding_threshold:
        return None

    entry = entries[top_idx]
    return Reply(
        text=entry["answer"],
        tier=Tier.EMBEDDINGS,
        latency_ms=latency_ms,
        meta={
            "matched_question": entry["question"],
            "similarity": round(top_score, 3),
            "threshold": settings.tardigrade_embedding_threshold,
            "tags": entry.get("tags", []),
        },
    )
