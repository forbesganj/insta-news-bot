"""Official Instagram API (Instagram API with Instagram Login) - publishing, insights, token refresh.

Endpoints used (all documented at developers.facebook.com/docs/instagram-platform):
  POST   graph.instagram.com/{version}/{ig-user-id}/media            create container (REELS + video_url)
  GET    graph.instagram.com/{version}/{container-id}?fields=status_code
  POST   graph.instagram.com/{version}/{ig-user-id}/media_publish    publish container
  GET    graph.instagram.com/{version}/{media-id}?fields=permalink,...
  GET    graph.instagram.com/{version}/{media-id}/insights?metric=...
  GET    graph.instagram.com/refresh_access_token                    long-lived token refresh
Permissions: instagram_business_basic, instagram_business_content_publish, instagram_business_manage_insights.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from ..net import Http

log = logging.getLogger(__name__)
BASE = "https://graph.instagram.com"
RATE_LIMIT_CODES = {4, 17, 32, 613}
TOKEN_CODES = {102, 190}


class MetaError(RuntimeError):
    def __init__(self, message: str, code: int | None = None, subcode: int | None = None,
                 status: int | None = None) -> None:
        super().__init__(message)
        self.code, self.subcode, self.status = code, subcode, status

    @property
    def kind(self) -> str:
        if self.code in TOKEN_CODES:
            return "token"
        if self.code in RATE_LIMIT_CODES:
            return "rate_limit"
        return "other"


class InstagramClient:
    def __init__(self, token: str, ig_user_id: str, http: Http, api_version: str = "v25.0",
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.token = token
        self.ig_user_id = ig_user_id
        self.http = http
        self.version = api_version
        self.sleep = sleep

    def _url(self, path: str) -> str:
        return f"{BASE}/{self.version}/{path}"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    @staticmethod
    def _parse(response) -> dict[str, Any]:  # noqa: ANN001
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400 or (isinstance(data, dict) and "error" in data):
            err = (data or {}).get("error", {}) if isinstance(data, dict) else {}
            raise MetaError(err.get("message") or f"HTTP {response.status_code}", err.get("code"),
                            err.get("error_subcode"), response.status_code)
        return data

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._parse(self.http.get(self._url(path), params=params or {}, headers=self._headers()))

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._parse(self.http.post(self._url(path), json=body, headers=self._headers(), timeout=60))

    # ------------------------------------------------------------- account
    def me(self) -> dict[str, Any]:
        return self._get("me", {"fields": "user_id,username"})

    def publishing_limit(self) -> dict[str, Any]:
        return self._get(f"{self.ig_user_id}/content_publishing_limit")

    # ----------------------------------------------------------- publishing
    def create_reel_container(self, video_url: str, caption: str) -> str:
        data = self._post(f"{self.ig_user_id}/media",
                          {"media_type": "REELS", "video_url": video_url, "caption": caption})
        if "id" not in data:
            raise MetaError(f"container creation returned no id: {data}")
        return str(data["id"])

    def container_status(self, container_id: str) -> str:
        return str(self._get(container_id, {"fields": "status_code"}).get("status_code", "UNKNOWN"))

    def wait_until_ready(self, container_id: str, first_wait: float = 15, interval: float = 60,
                         max_wait: float = 300) -> None:
        """Meta recommends polling about once a minute for at most five minutes."""
        self.sleep(first_wait)
        waited = first_wait
        while True:
            status = self.container_status(container_id)
            log.info("container %s status %s", container_id, status)
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                raise MetaError(f"container {container_id} ended with status {status}")
            if waited >= max_wait:
                raise MetaError(f"container {container_id} not ready after {waited:.0f}s (status {status})")
            self.sleep(interval)
            waited += interval

    def publish_container(self, container_id: str) -> str:
        data = self._post(f"{self.ig_user_id}/media_publish", {"creation_id": container_id})
        if "id" not in data:
            raise MetaError(f"media_publish returned no id: {data}")
        return str(data["id"])

    def media_info(self, media_id: str) -> dict[str, Any]:
        return self._get(media_id, {"fields": "permalink,media_product_type,timestamp"})

    def publish_reel(self, video_url: str, caption: str, first_wait: float = 15, interval: float = 60,
                     max_wait: float = 300, on_container: Callable[[str], None] | None = None) -> dict[str, Any]:
        container = self.create_reel_container(video_url, caption)
        if on_container:
            on_container(container)
        self.wait_until_ready(container, first_wait, interval, max_wait)
        media_id = self.publish_container(container)
        permalink = None
        try:
            permalink = self.media_info(media_id).get("permalink")
        except MetaError as exc:
            log.warning("published %s but could not read permalink: %s", media_id, exc)
        return {"container_id": container, "media_id": media_id, "permalink": permalink}

    # ------------------------------------------------------------- insights
    @staticmethod
    def _flatten(data: dict[str, Any]) -> dict[str, float]:
        out: dict[str, float] = {}
        for entry in data.get("data", []):
            value = None
            if entry.get("values"):
                value = entry["values"][0].get("value")
            elif "total_value" in entry:
                value = entry["total_value"].get("value")
            if isinstance(value, (int, float)):
                out[entry["name"]] = float(value)
        return out

    def media_insights(self, media_id: str, metrics: list[str]) -> tuple[dict[str, float], list[str]]:
        """Returns (values, unsupported). Falls back to one call per metric if the batch call is rejected."""
        try:
            return self._flatten(self._get(f"{media_id}/insights", {"metric": ",".join(metrics)})), []
        except MetaError as exc:
            if exc.kind in ("token", "rate_limit"):
                raise
        values: dict[str, float] = {}
        unsupported: list[str] = []
        for metric in metrics:
            try:
                values.update(self._flatten(self._get(f"{media_id}/insights", {"metric": metric})))
            except MetaError as exc:
                if exc.kind in ("token", "rate_limit"):
                    raise
                unsupported.append(metric)
        return values, unsupported


def refresh_long_lived_token(http: Http, token: str) -> dict[str, Any]:
    """Long-lived tokens last 60 days and can be refreshed once they are at least 24 hours old."""
    response = http.get(f"{BASE}/refresh_access_token",
                        params={"grant_type": "ig_refresh_token", "access_token": token})
    return InstagramClient._parse(response)
