"""News and trend collectors. Every collector is isolated: a failing source never stops a run."""
from __future__ import annotations

import logging
from datetime import timedelta

from ..models import RawItem
from ..net import Http
from ..settings import Settings
from ..store import iso, parse_iso, utcnow
from . import google_trends, hackernews, reddit, rss

log = logging.getLogger(__name__)


def collect_all(settings: Settings, http: Http) -> tuple[list[RawItem], dict[str, int]]:
    """Run every enabled collector. Returns (items, per-source counts)."""
    cfg = settings.sources or {}
    max_age = float(settings.get("collect.max_age_hours", 36))
    cutoff = utcnow() - timedelta(hours=max_age)
    items: list[RawItem] = []
    counts: dict[str, int] = {}

    def run(name: str, func) -> None:  # noqa: ANN001
        try:
            got = func()
        except Exception as exc:  # noqa: BLE001 - isolation is the point
            log.warning("source %s failed: %s", name, exc)
            counts[name] = 0
            return
        counts[name] = len(got)
        items.extend(got)

    for feed in cfg.get("rss", []) or []:
        run(f"rss:{feed.get('name', feed.get('url'))}", lambda f=feed: rss.fetch_feed(http, f, cfg, settings))
    for query in (cfg.get("google_news", {}) or {}).get("queries", []) or []:
        run(f"gnews:{query}", lambda q=query: rss.fetch_google_news(http, q, cfg, settings))
    if (cfg.get("hackernews", {}) or {}).get("enabled", False):
        run("hackernews", lambda: hackernews.fetch(http, cfg, settings))
    if (cfg.get("google_trends", {}) or {}).get("enabled", False):
        for geo in cfg["google_trends"].get("geos", ["IN"]):
            run(f"gtrends:{geo}", lambda g=geo: google_trends.fetch(http, g, cfg, settings))
    if (cfg.get("reddit", {}) or {}).get("enabled", False):
        run("reddit", lambda: reddit.fetch(http, cfg, settings))

    fresh: list[RawItem] = []
    for item in items:
        published = parse_iso(item.published_at)
        if published is not None and published < cutoff:
            continue
        if not item.published_at:
            item.published_at = iso(utcnow())
        fresh.append(item)
    log.info("collected %d items (%d fresh) from %d sources", len(items), len(fresh), len(counts))
    return fresh, counts
