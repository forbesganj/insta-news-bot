"""RSS / Atom / Google News RSS collectors (standard library XML parsing)."""
from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

from ..models import RawItem
from ..net import Http
from ..settings import Settings
from ..store import iso, utcnow
from ..textutil import domain_of, strip_html, strip_publisher_suffix

log = logging.getLogger(__name__)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _child_text(node: ET.Element, *names: str) -> str:
    for child in node:
        if _local(child.tag) in names and (child.text or "").strip():
            return child.text.strip()
    return ""


def parse_date(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return iso(dt)
    except (TypeError, ValueError):
        pass
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return iso(dt)
    except ValueError:
        return ""


def tier_for(domain: str, sources_cfg: dict, default: int = 3) -> int:
    tiers = (sources_cfg or {}).get("tiers", {}) or {}
    if domain in (tiers.get("tier1_domains") or []):
        return 1
    if domain in (tiers.get("tier2_domains") or []):
        return 2
    return default


def parse_feed(xml_text: str, feed: dict, sources_cfg: dict, max_items: int = 25) -> list[RawItem]:
    if re.search(r"<!DOCTYPE|<!ENTITY", xml_text[:4000], re.IGNORECASE):
        raise ValueError("DTD/entity declarations are not accepted in feeds")
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    entries = [n for n in root.iter() if _local(n.tag) in ("item", "entry")]
    out: list[RawItem] = []
    for node in entries[:max_items]:
        title = html.unescape(_child_text(node, "title"))
        link = _child_text(node, "link")
        if not link:
            for child in node:
                if _local(child.tag) == "link" and child.get("href"):
                    link = child.get("href")
                    if child.get("rel", "alternate") == "alternate":
                        break
        if not title or not link:
            continue
        summary = strip_html(html.unescape(_child_text(node, "description", "summary", "content", "encoded")))
        published = parse_date(_child_text(node, "pubDate", "published", "updated", "date"))
        source_name = feed.get("name", "")
        source_url = ""
        fetchable = bool(feed.get("fetchable", True))
        for child in node:
            if _local(child.tag) == "source" and child.get("url"):
                source_url = child.get("url", "")
                source_name = (child.text or source_name).strip() or source_name
        if feed.get("google_news") and source_url:
            domain = domain_of(source_url)
            title = strip_publisher_suffix(title)
            fetchable = False  # news.google.com links are redirects; use as corroboration signal only
        else:
            domain = domain_of(link)
        default_tier = int(feed.get("tier", 3))
        out.append(RawItem(
            title=title, url=link, source=source_name or domain, domain=domain,
            tier=tier_for(domain, sources_cfg, default_tier) if feed.get("google_news") else min(
                default_tier, tier_for(domain, sources_cfg, default_tier)),
            published_at=published, summary=summary[:1500],
            origin="gnews" if feed.get("google_news") else "rss", fetchable=fetchable,
            extra={"feed": feed.get("name", ""), "pillar_hint": feed.get("pillar_hint", "")},
        ))
    return out


def fetch_feed(http: Http, feed: dict, sources_cfg: dict, settings: Settings) -> list[RawItem]:
    url = feed["url"]
    response = http.get(url)
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code} for {url}")
    max_items = int(settings.get("collect.max_items_per_feed", 25))
    return parse_feed(response.text, feed, sources_cfg, max_items)


def fetch_google_news(http: Http, query: str, sources_cfg: dict, settings: Settings) -> list[RawItem]:
    gn = (sources_cfg.get("google_news") or {})
    hl, gl, ceid = gn.get("hl", "en-IN"), gn.get("gl", "IN"), gn.get("ceid", "IN:en")
    when = gn.get("when", "1d")
    url = (f"https://news.google.com/rss/search?q={quote_plus(query + ' when:' + when)}"
           f"&hl={hl}&gl={gl}&ceid={ceid}")
    feed = {"name": f"Google News: {query}", "tier": 3, "google_news": True, "fetchable": False}
    response = http.get(url)
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code} for Google News query {query!r}")
    return parse_feed(response.text, feed, sources_cfg, int(settings.get("collect.max_items_per_feed", 25)))
