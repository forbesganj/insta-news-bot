"""Pulls insights for published posts at 24h / 72h / 7d checkpoints."""
from __future__ import annotations

import logging
from datetime import datetime

from ..publish.meta import InstagramClient, MetaError
from ..settings import Settings
from ..store import Store, parse_iso, utcnow

log = logging.getLogger(__name__)


def collect(store: Store, settings: Settings, client: InstagramClient, now: datetime | None = None) -> dict[str, int]:
    now = now or utcnow()
    checkpoints = sorted(int(c) for c in settings.get("analytics.checkpoints_hours", [24, 72, 168]))
    metrics = list(settings.get("analytics.metrics", []))
    stats = {"posts": 0, "fetched": 0, "skipped": 0, "errors": 0}
    for post in store.list_posts(real_only=True):
        stats["posts"] += 1
        published = parse_iso(post["published_at"])
        if not published or not post.get("media_id"):
            stats["skipped"] += 1
            continue
        age_h = (now - published).total_seconds() / 3600.0
        due = [c for c in checkpoints if c <= age_h]
        if not due or store.has_metrics(post["id"], due[-1]):
            stats["skipped"] += 1
            continue
        checkpoint = due[-1]
        try:
            values, unsupported = client.media_insights(post["media_id"], metrics)
        except MetaError as exc:
            stats["errors"] += 1
            log.warning("insights failed for %s: %s", post["media_id"], exc)
            if exc.kind in ("token", "rate_limit"):
                raise
            continue
        if unsupported:
            log.info("metrics not supported for %s: %s", post["media_id"], unsupported)
        store.save_metrics(post["id"], checkpoint, values, None, {"values": values, "unsupported": unsupported})
        stats["fetched"] += 1
    log.info("analytics: %s", stats)
    return stats
