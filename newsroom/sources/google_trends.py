"""Google Trends 'Trending now' RSS (unofficial public feed; failures are tolerated)."""
from __future__ import annotations

import html
import xml.etree.ElementTree as ET

from ..models import RawItem
from ..net import Http
from ..settings import Settings
from ..store import iso, utcnow
from ..textutil import domain_of
from .rss import _local, parse_date, tier_for

FEED = "https://trends.google.com/trending/rss?geo={geo}"


def parse(xml_text: str, sources_cfg: dict) -> list[RawItem]:
    root = ET.fromstring(xml_text.encode("utf-8"))
    out: list[RawItem] = []
    for item in [n for n in root.iter() if _local(n.tag) == "item"]:
        query = ""
        traffic = ""
        published = ""
        news: list[dict[str, str]] = []
        for child in item:
            name = _local(child.tag)
            if name == "title":
                query = html.unescape((child.text or "").strip())
            elif name == "approx_traffic":
                traffic = (child.text or "").strip()
            elif name == "pubDate":
                published = parse_date(child.text or "")
            elif name == "news_item":
                entry = {_local(sub.tag): html.unescape((sub.text or "").strip()) for sub in child}
                news.append(entry)
        for entry in news:
            url = entry.get("news_item_url", "")
            title = entry.get("news_item_title", "")
            if not url or not title:
                continue
            domain = domain_of(url)
            out.append(RawItem(
                title=title, url=url, source=entry.get("news_item_source") or domain, domain=domain,
                tier=tier_for(domain, sources_cfg, 3), published_at=published or iso(utcnow()),
                summary="", origin="gtrends", extra={"trend_query": query, "traffic": traffic},
            ))
    return out


def fetch(http: Http, geo: str, sources_cfg: dict, settings: Settings) -> list[RawItem]:
    response = http.get(FEED.format(geo=geo))
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code} from Google Trends ({geo})")
    return parse(response.text, sources_cfg)
