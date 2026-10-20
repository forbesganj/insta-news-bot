"""Story scoring and selection (learned multipliers + epsilon-greedy exploration)."""
from __future__ import annotations

import math
import random
import re
from datetime import datetime
from typing import Any

from .pillars import fit_score
from .settings import Settings
from .store import parse_iso, utcnow
from .verify import independent_groups


def newest_published(story: dict[str, Any]) -> datetime | None:
    dates = [parse_iso(s.get("published_at")) for s in story.get("sources", [])]
    dates = [d for d in dates if d]
    return max(dates) if dates else None


def trend_signal(story: dict[str, Any]) -> float:
    best = 0.0
    for source in story.get("sources", []):
        extra = source.get("extra", {}) or {}
        if extra.get("hn_score"):
            best = max(best, min(1.0, math.log10(max(int(extra["hn_score"]), 1)) / 3.0))
        if extra.get("reddit_score"):
            best = max(best, min(1.0, math.log10(max(int(extra["reddit_score"]), 1)) / 4.0))
        if source.get("origin") == "gtrends":
            best = max(best, 0.8)
    return best


def potential_signal(title: str) -> float:
    score = 0.0
    if re.search(r"\d", title):
        score += 0.5
    if re.search(r"[\u20b9$%]|\b(?:crore|lakh|billion|million|trillion)\b", title, re.IGNORECASE):
        score += 0.3
    n_words = len(title.split())
    if 6 <= n_words <= 14:
        score += 0.2
    return min(1.0, score)


def score_story(story: dict[str, Any], settings: Settings, multipliers: dict[str, float] | None = None,
                now: datetime | None = None) -> tuple[float, dict[str, float]]:
    now = now or utcnow()
    weights = settings.get("score.weights", {}) or {}
    half_life = float(settings.get("score.half_life_hours", 12.0))
    published = newest_published(story) or now
    age_h = max((now - published).total_seconds() / 3600.0, 0.0)
    recency = 0.5 ** (age_h / half_life)
    groups = independent_groups(story.get("sources", []), settings)
    cross = min(1.0, max(len(groups) - 1, 0) / 3.0)
    best_tier = min((int(s.get("tier", 3)) for s in story.get("sources", [])), default=3)
    tier = {1: 1.0, 2: 0.6}.get(best_tier, 0.3)
    fit = fit_score(story.get("title", ""), settings.pillars)
    trend = trend_signal(story)
    potential = potential_signal(story.get("title", ""))
    base = (weights.get("recency", 0.25) * recency + weights.get("cross_source", 0.25) * cross
            + weights.get("tier", 0.15) * tier + weights.get("fit", 0.15) * fit
            + weights.get("trend", 0.10) * trend + weights.get("potential", 0.10) * potential) * 100.0
    multiplier = (multipliers or {}).get(story.get("pillar") or "", 1.0)
    total = base * multiplier
    detail = {"recency": round(recency, 3), "cross_source": round(cross, 3), "tier": tier, "fit": round(fit, 3),
              "trend": round(trend, 3), "potential": round(potential, 3), "multiplier": round(multiplier, 3),
              "base": round(base, 2)}
    return round(total, 2), detail


def select_stories(candidates: list[dict[str, Any]], n: int, epsilon: float, rng: random.Random) -> list[dict[str, Any]]:
    """Top-n by score, but with probability `epsilon` per slot pick randomly from the next tier (exploration)."""
    pool = sorted(candidates, key=lambda s: -float(s.get("score") or 0.0))
    chosen: list[dict[str, Any]] = []
    while pool and len(chosen) < n:
        if rng.random() < epsilon and len(pool) > 1:
            window = pool[: max(2, n * 2)]
            pick = rng.choice(window)
        else:
            pick = pool[0]
        chosen.append(pick)
        pool.remove(pick)
    return chosen
