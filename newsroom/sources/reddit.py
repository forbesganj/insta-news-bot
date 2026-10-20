"""Reddit via the official OAuth API. Disabled by default; requires REDDIT_CLIENT_ID/SECRET.

Reddit's Data API terms limit commercial use; read them before enabling this source.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..models import RawItem
from ..net import Http
from ..settings import Settings, env
from ..store import iso
from ..textutil import domain_of
from .rss import tier_for

log = logging.getLogger(__name__)


def fetch(http: Http, sources_cfg: dict, settings: Settings) -> list[RawItem]:
    client_id = env("REDDIT_CLIENT_ID")
    client_secret = env("REDDIT_CLIENT_SECRET")
    user_agent = env("REDDIT_USER_AGENT", "newsroom-bot/1.0 by u/your_username")
    if not client_id or not client_secret:
        log.info("reddit enabled but REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET not set; skipping")
        return []
    cfg = sources_cfg.get("reddit", {}) or {}
    token_response = http.post(
        "https://www.reddit.com/api/v1/access_token",
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        headers={"User-Agent": user_agent},
    )
    token_response.raise_for_status()
    token = token_response.json().get("access_token")
    if not token:
        raise RuntimeError("Reddit did not return an access token")
    headers = {"Authorization": f"bearer {token}", "User-Agent": user_agent}
    out: list[RawItem] = []
    min_score = int(cfg.get("min_score", 300))
    for sub in cfg.get("subreddits", []) or []:
        response = http.get(f"https://oauth.reddit.com/r/{sub}/hot", params={"limit": 25}, headers=headers)
        if response.status_code != 200:
            log.warning("reddit r/%s -> HTTP %s", sub, response.status_code)
            continue
        for child in response.json().get("data", {}).get("children", []):
            data = child.get("data", {})
            if data.get("is_self") or data.get("stickied") or int(data.get("score", 0)) < min_score:
                continue
            url = data.get("url_overridden_by_dest") or data.get("url") or ""
            if not url.startswith("http"):
                continue
            domain = domain_of(url)
            out.append(RawItem(
                title=data.get("title", "").strip(), url=url, source=f"r/{sub}", domain=domain,
                tier=tier_for(domain, sources_cfg, 3),
                published_at=iso(datetime.fromtimestamp(float(data.get("created_utc", 0)), tz=timezone.utc)),
                origin="reddit", extra={"reddit_score": int(data.get("score", 0))},
            ))
    return out
