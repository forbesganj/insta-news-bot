"""Source verification gate. Fails closed: if it cannot prove a story is solid, it rejects it."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .models import Fact, VerificationResult
from .settings import Settings
from .store import parse_iso, utcnow


def _group_map(settings: Settings) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for group in (settings.sources or {}).get("syndication_groups", []) or []:
        for domain in group:
            mapping[domain] = group[0]
    return mapping


def independent_groups(sources: list[dict[str, Any]], settings: Settings) -> set[str]:
    block = set((settings.sources or {}).get("blocklist_domains", []) or [])
    mapping = _group_map(settings)
    out: set[str] = set()
    for source in sources:
        domain = source.get("domain", "")
        if not domain or domain in block:
            continue
        out.add(mapping.get(domain, domain))
    return out


def verify_story(story: dict[str, Any], facts: list[Fact], settings: Settings,
                 now: datetime | None = None) -> VerificationResult:
    cfg = settings.get("verify", {}) or {}
    now = now or utcnow()
    sources = story.get("sources", [])
    block = set((settings.sources or {}).get("blocklist_domains", []) or [])
    usable = [s for s in sources if s.get("domain") not in block]
    reasons: list[str] = []
    if not usable:
        return VerificationResult(False, 0.0, ["verify: no usable (non-blocklisted) sources"])

    groups = independent_groups(usable, settings)
    best_tier = min(int(s.get("tier", 3)) for s in usable)
    needed = int(cfg.get("min_independent_sources", 2))
    passed = True

    if len(groups) < needed and not (cfg.get("allow_single_tier1", True) and best_tier == 1):
        passed = False
        reasons.append(f"verify: only {len(groups)} independent source(s), need {needed}")

    newest = max((parse_iso(s.get("published_at")) or datetime.min.replace(tzinfo=now.tzinfo) for s in usable))
    age_hours = (now - newest).total_seconds() / 3600.0
    if age_hours > float(cfg.get("max_age_hours", 36)):
        passed = False
        reasons.append(f"verify: stale story ({age_hours:.0f}h old)")

    min_facts = int(cfg.get("min_facts", 3))
    if len(facts) < min_facts:
        passed = False
        reasons.append(f"verify: only {len(facts)} grounded fact(s), need {min_facts}")

    blob = " ".join([story.get("title", "")] + [f.text for f in facts]).lower()

    for pattern in cfg.get("reject_patterns", []) or []:
        if re.search(pattern, blob, re.IGNORECASE):
            passed = False
            reasons.append("verify: investment-advice topic (not covered by this account)")
            break

    needs_review = False
    rumor_hit = any(re.search(p, blob, re.IGNORECASE) for p in cfg.get("rumor_patterns", []) or [])
    if rumor_hit:
        if best_tier != 1 and len(groups) < 3:
            passed = False
            reasons.append("verify: rumour language without strong confirmation (tier-1 source or 3+ outlets)")
        else:
            needs_review = True
            reasons.append("note: rumour language present but confirmed; human review required")

    sensitivity = "low"
    sensitive_words = [w for w in cfg.get("sensitive_keywords", []) or []
                       if re.search(r"\b" + re.escape(w) + r"\b", blob)]
    if sensitive_words:
        sensitivity = "high"
        needs_review = True
        reasons.append("note: sensitive topic (" + ", ".join(sorted(sensitive_words)[:4]) + "); human review required")
    elif best_tier >= 3 and len(groups) < 3:
        sensitivity = "medium"

    block_words = [w for w in cfg.get("mascot_block_keywords", []) or [] if re.search(r"\b" + re.escape(w) + r"\b", blob)]
    meme_blocked = sensitivity == "high" or bool(block_words)
    if block_words:
        reasons.append("note: mascot disabled (" + ", ".join(sorted(block_words)[:3]) + ")")

    confidence = 0.35
    confidence += 0.2 * min(max(len(groups) - 1, 0), 3) / 1.0 * 0.5 * 2 / 2
    confidence += 0.2 if best_tier == 1 else (0.1 if best_tier == 2 else 0.0)
    confidence += 0.1 if len(facts) >= 5 else 0.0
    confidence -= 0.2 if rumor_hit else 0.0
    confidence = max(0.0, min(1.0, confidence))
    if passed:
        reasons.insert(0, f"verified: {len(groups)} independent source(s), best tier {best_tier}, {len(facts)} facts")
    return VerificationResult(passed=passed, confidence=round(confidence, 2), reasons=reasons,
                              sensitivity=sensitivity, needs_review=needs_review,
                              independent_sources=len(groups), best_tier=best_tier, meme_blocked=meme_blocked)
