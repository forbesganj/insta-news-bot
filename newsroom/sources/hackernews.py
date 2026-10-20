"""Hacker News via the official Firebase API (https://github.com/HackerNews/API)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..models import RawItem
from ..net import Http
from ..settings import Settings
from ..store import iso
from ..textutil import domain_of
from .rss import tier_for

log = logging.getLogger(__name__)
BASE = "https://hacker-news.firebaseio.com/v0"


def fetch(http: Http, sources_cfg: dict, settings: Settings) -> list[RawItem]:
    cfg = sources_cfg.get("hackernews", {}) or {}
    top_n = int(cfg.get("top_n", 40))
    min_score = int(cfg.get("min_score", 150))
    ids = http.get(f"{BASE}/topstories.json").json()[:top_n]
    out: list[RawItem] = []
    for story_id in ids:
        try:
            item = http.get(f"{BASE}/item/{story_id}.json").json()
        except Exception as exc:  # noqa: BLE001
            log.debug("HN item %s failed: %s", story_id, exc)
            continue
        if not item or item.get("type") != "story" or not item.get("url"):
            continue
        score = int(item.get("score", 0))
        if score < min_score:
            continue
        domain = domain_of(item["url"])
        published = iso(datetime.fromtimestamp(int(item.get("time", 0)), tz=timezone.utc))
        out.append(RawItem(
            title=item.get("title", "").strip(), url=item["url"], source="Hacker News", domain=domain,
            tier=tier_for(domain, sources_cfg, 3), published_at=published, summary="", origin="hn",
            extra={"hn_score": score, "hn_comments": int(item.get("descendants", 0))},
        ))
    return out
