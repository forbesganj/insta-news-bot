"""Clustering and duplicate prevention."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .models import RawItem
from .pillars import classify_pillar
from .settings import Settings
from .store import Store
from .textutil import jaccard, overlap, tokens, url_hash

log = logging.getLogger(__name__)

POSTED_STATUSES = ("published", "dry_published", "queued", "scripted", "publishing", "needs_manual_check")


@dataclass
class Cluster:
    items: list[RawItem] = field(default_factory=list)
    tokens: set[str] = field(default_factory=set)

    def add(self, item: RawItem, item_tokens: set[str]) -> None:
        self.items.append(item)
        self.tokens |= item_tokens

    @property
    def title(self) -> str:
        ranked = sorted(self.items, key=lambda i: (i.tier, -len(i.summary), len(i.title)))
        return ranked[0].title


def similar(a: set[str], b: set[str], cfg: dict) -> bool:
    if jaccard(a, b) >= float(cfg.get("cluster_jaccard", 0.40)):
        return True
    ratio, shared = overlap(a, b)
    return ratio >= float(cfg.get("cluster_overlap", 0.65)) and shared >= int(cfg.get("cluster_min_shared", 3))


def cluster_items(items: list[RawItem], cfg: dict) -> list[Cluster]:
    clusters: list[Cluster] = []
    for item in items:
        item_tokens = tokens(item.title)
        if len(item_tokens) < 2:
            continue
        placed = False
        for cluster in clusters:
            # compare against each member, not the union, to avoid topic drift
            if any(similar(item_tokens, tokens(member.title), cfg) for member in cluster.items):
                cluster.add(item, item_tokens)
                placed = True
                break
        if not placed:
            cluster = Cluster()
            cluster.add(item, item_tokens)
            clusters.append(cluster)
    return clusters


def find_duplicate(store: Store, title_tokens: set[str], cfg: dict, statuses: tuple[str, ...] = POSTED_STATUSES,
                   days: int | None = None, exclude_id: str | None = None) -> dict[str, Any] | None:
    window = int(days if days is not None else cfg.get("posted_block_days", 7))
    threshold = float(cfg.get("posted_jaccard", 0.5))
    for story in store.recent_stories(window, statuses):
        if exclude_id and story["id"] == exclude_id:
            continue
        other = set(story["tokens"]) if isinstance(story["tokens"], list) else set()
        if jaccard(title_tokens, other) >= threshold or similar(title_tokens, other, cfg):
            return story
    return None


def ingest(store: Store, items: list[RawItem], settings: Settings) -> dict[str, int]:
    """Deduplicate raw items, cluster them, and create/merge stories. Returns stats."""
    cfg = settings.get("dedupe", {}) or {}
    stats = {"items_in": len(items), "items_seen": 0, "new_stories": 0, "merged": 0,
             "duplicates": 0, "reopened": 0}
    fresh: list[RawItem] = []
    for item in items:
        if store.item_seen(url_hash(item.url)):
            stats["items_seen"] += 1
            continue
        fresh.append(item)
    clusters = cluster_items(fresh, cfg)
    existing = store.recent_stories(int(cfg.get("history_days", 14)))
    for cluster in clusters:
        match = None
        for story in existing:
            other = set(story["tokens"]) if isinstance(story["tokens"], list) else set()
            if similar(cluster.tokens, other, cfg):
                match = story
                break
        sources = [i.to_dict() for i in cluster.items]
        if match is None:
            hint = next((i.extra.get("pillar_hint") for i in cluster.items if i.extra.get("pillar_hint")), None)
            pillar = classify_pillar(cluster.title + " " + " ".join(i.summary for i in cluster.items),
                                     settings.pillars, hint)
            story_id = store.create_story(cluster.title, cluster.tokens, sources, pillar)
            created = store.get_story(story_id)
            if created:
                existing.append(created)
            stats["new_stories"] += 1
        else:
            story_id = match["id"]
            status = match["status"]
            if status in ("candidate", "verified"):
                known = {s["url"] for s in match["sources"]}
                added = [s for s in sources if s["url"] not in known]
                if added:
                    store.update_story(story_id, sources=match["sources"] + added)
                    match["sources"] = match["sources"] + added
                stats["merged"] += 1
            elif status == "rejected" and str(match.get("last_error") or "").startswith("verify:"):
                meta = match.get("meta") or {}
                reopen_count = int(meta.get("reopen_count", 0)) if isinstance(meta, dict) else 0
                known_domains = {s["domain"] for s in match["sources"]}
                new_domains = {s["domain"] for s in sources} - known_domains
                if new_domains and reopen_count < 2:
                    merged_sources = match["sources"] + [s for s in sources if s["domain"] in new_domains]
                    store.update_story(story_id, sources=merged_sources, status="candidate", last_error=None)
                    store.merge_meta(story_id, reopen_count=reopen_count + 1)
                    stats["reopened"] += 1
                else:
                    stats["duplicates"] += 1
            else:
                stats["duplicates"] += 1
        for item in cluster.items:
            store.add_item(url_hash(item.url), item.to_dict(), story_id)
    log.info("ingest: %s", stats)
    return stats
