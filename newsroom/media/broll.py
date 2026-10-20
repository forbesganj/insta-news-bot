"""Optional stock B-roll from the Pexels Videos API (free key: PEXELS_API_KEY). Pexels licence: free to use."""
from __future__ import annotations

import logging
from pathlib import Path

from ..net import Http
from ..settings import env

log = logging.getLogger(__name__)
API = "https://api.pexels.com/videos/search"
MAX_BYTES = 25_000_000


def pick_file(video: dict) -> dict | None:
    """Choose a portrait mp4 close to 1080 wide, never above it."""
    files = [f for f in video.get("video_files", [])
             if f.get("file_type") == "video/mp4" and f.get("width") and f.get("height")
             and 540 <= int(f["width"]) <= 1080 and int(f["height"]) > int(f["width"])]
    return max(files, key=lambda f: int(f["width"])) if files else None


def fetch_clips(http: Http, keywords: list[str], work_dir: Path, max_clips: int = 2) -> list[Path]:
    key = env("PEXELS_API_KEY")
    if not key or not keywords or http.offline:
        return []
    clips: list[Path] = []
    try:
        for query in dict.fromkeys([" ".join(keywords[:2]), keywords[0]]):
            if len(clips) >= max_clips:
                break
            response = http.get(API, params={"query": query, "orientation": "portrait", "size": "medium",
                                             "per_page": 8}, headers={"Authorization": key})
            if response.status_code != 200:
                log.info("pexels videos HTTP %s for %r", response.status_code, query)
                continue
            for video in response.json().get("videos", []):
                if len(clips) >= max_clips:
                    break
                chosen = pick_file(video)
                if not chosen:
                    continue
                data = http.get(chosen["link"])
                if data.status_code != 200 or len(data.content) > MAX_BYTES:
                    continue
                path = work_dir / f"broll_{video['id']}.mp4"
                path.write_bytes(data.content)
                clips.append(path)
    except Exception as exc:  # noqa: BLE001 - B-roll is a nicety, never fatal
        log.info("b-roll unavailable: %s", exc)
    return clips
