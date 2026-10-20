"""Pillar classification and niche-fit scoring from config/pillars.yaml."""
from __future__ import annotations

import re


def _hits(text: str, keywords: list[str]) -> int:
    lowered = " " + re.sub(r"[^a-z0-9+#]+", " ", (text or "").lower()) + " "
    return sum(1 for kw in keywords if " " + kw.lower() + " " in lowered)


def classify_pillar(text: str, pillars_cfg: dict, hint: str | None = None) -> str:
    pillars = (pillars_cfg or {}).get("pillars", {}) or {}
    default = (pillars_cfg or {}).get("default", "tech")
    best, best_hits = None, 0
    for name, spec in pillars.items():
        hits = _hits(text, spec.get("keywords", []))
        if hits > best_hits:
            best, best_hits = name, hits
    if best:
        return best
    if hint and hint in pillars:
        return hint
    return default


def fit_score(text: str, pillars_cfg: dict) -> float:
    """0..1: how strongly the text matches ANY pillar (3+ keyword hits = 1.0)."""
    pillars = (pillars_cfg or {}).get("pillars", {}) or {}
    best = 0
    for spec in pillars.values():
        best = max(best, _hits(text, spec.get("keywords", [])))
    return min(1.0, best / 3.0)


def pillar_hashtags(pillar: str, pillars_cfg: dict) -> list[str]:
    spec = ((pillars_cfg or {}).get("pillars", {}) or {}).get(pillar, {})
    return list(spec.get("hashtags", []))
