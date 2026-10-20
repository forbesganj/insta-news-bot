"""HTTP layer: retries, timeouts, robots.txt, file:// support for tests and demos."""
from __future__ import annotations

import logging
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlsplit

import requests

log = logging.getLogger(__name__)

DEFAULT_UA = "newsroom-bot/1.0 (+https://github.com; news research; contact via repository owner)"


class OfflineError(RuntimeError):
    """Raised when a network request is attempted in offline mode."""


class LocalResponse:
    """Minimal stand-in for requests.Response for file:// URLs."""

    def __init__(self, path: Path) -> None:
        self.status_code = 200
        self.content = path.read_bytes()
        self.text = self.content.decode("utf-8", errors="replace")
        self.headers = {"Content-Type": "text/xml"}
        self.url = path.as_uri()

    def raise_for_status(self) -> None:
        return None

    def json(self):  # noqa: ANN201
        import json

        return json.loads(self.text)


class Http:
    def __init__(self, user_agent: str = DEFAULT_UA, timeout: float = 20.0, retries: int = 2,
                 backoff: float = 1.5, offline: bool = False) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.offline = offline
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "*/*"})
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def get(self, url: str, **kwargs):  # noqa: ANN201
        if url.startswith("file://"):
            return LocalResponse(Path(urlsplit(url).path))
        if self.offline:
            raise OfflineError(f"offline mode: refusing GET {url}")
        kwargs.setdefault("timeout", self.timeout)
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self.session.get(url, **kwargs)
                if response.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    delay = self.backoff * (2 ** attempt)
                    retry_after = response.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        delay = min(float(retry_after), 30.0)
                    log.warning("GET %s -> %s, retry in %.1fs", url, response.status_code, delay)
                    time.sleep(delay)
                    continue
                return response
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < self.retries:
                    time.sleep(self.backoff * (2 ** attempt))
        raise last_exc if last_exc else RuntimeError("request failed")

    def post(self, url: str, **kwargs):  # noqa: ANN201
        """POST without automatic retries (publishing calls must not be repeated blindly)."""
        if self.offline:
            raise OfflineError(f"offline mode: refusing POST {url}")
        kwargs.setdefault("timeout", self.timeout)
        return self.session.post(url, **kwargs)

    def delete(self, url: str, **kwargs):  # noqa: ANN201
        if self.offline:
            raise OfflineError(f"offline mode: refusing DELETE {url}")
        kwargs.setdefault("timeout", self.timeout)
        return self.session.delete(url, **kwargs)

    def head(self, url: str, **kwargs):  # noqa: ANN201
        if self.offline:
            raise OfflineError(f"offline mode: refusing HEAD {url}")
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("allow_redirects", True)
        return self.session.head(url, **kwargs)

    def robots_allowed(self, url: str) -> bool:
        """RFC 9309: 4xx -> allowed, 5xx/unreachable -> disallowed."""
        if url.startswith("file://"):
            return True
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser = urllib.robotparser.RobotFileParser()
            try:
                response = self.get(origin + "/robots.txt", timeout=10)
                if response.status_code == 200:
                    parser.parse(response.text.splitlines())
                    self._robots[origin] = parser
                elif 400 <= response.status_code < 500:
                    self._robots[origin] = None  # no robots.txt: everything allowed
                else:
                    parser.disallow_all = True
                    self._robots[origin] = parser
            except (requests.RequestException, OfflineError):
                parser.disallow_all = True
                self._robots[origin] = parser
        parser = self._robots[origin]
        return True if parser is None else parser.can_fetch(self.user_agent, url)
