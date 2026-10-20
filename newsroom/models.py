"""Plain data containers shared across the pipeline."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class RawItem:
    title: str
    url: str
    source: str
    domain: str
    tier: int = 3
    published_at: str = ""      # ISO-8601 UTC
    summary: str = ""
    origin: str = "rss"         # rss | hn | gtrends | reddit | gnews
    fetchable: bool = True      # False when the URL cannot be read directly (e.g. Google News redirects)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Fact:
    text: str
    evidence: str
    source_url: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class VerificationResult:
    passed: bool
    confidence: float
    reasons: list[str] = field(default_factory=list)
    sensitivity: str = "low"          # low | medium | high
    needs_review: bool = False
    independent_sources: int = 0
    best_tier: int = 3
    meme_blocked: bool = False         # mascot/meme humour must not be used on this story

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GateResult:
    name: str
    passed: bool
    score: float | None = None
    engine: str = "builtin"           # upstream | builtin
    details: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
