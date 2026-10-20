"""Shared test helpers."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class FakeResponse:
    def __init__(self, status: int = 200, payload=None, text: str = "", headers=None) -> None:  # noqa: ANN001
        self.status_code = status
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")
        self.headers = headers or {}
        self.content = self.text.encode()

    def json(self):  # noqa: ANN201
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeHttp:
    """Scripted HTTP double: routes are (method, substring) -> list of responses consumed in order."""

    def __init__(self, routes: dict) -> None:
        self.routes = {k: list(v) for k, v in routes.items()}
        self.calls: list[tuple[str, str, dict]] = []
        self.offline = False

    def _handle(self, method: str, url: str, **kwargs):  # noqa: ANN001, ANN202
        self.calls.append((method, url, kwargs))
        for (m, fragment), queue in self.routes.items():
            if m == method and fragment in url:
                return queue.pop(0) if len(queue) > 1 else queue[0]
        raise AssertionError(f"unexpected {method} {url}")

    def get(self, url, **kw):  # noqa: ANN001, ANN201
        return self._handle("GET", url, **kw)

    def post(self, url, **kw):  # noqa: ANN001, ANN201
        return self._handle("POST", url, **kw)

    def head(self, url, **kw):  # noqa: ANN001, ANN201
        return self._handle("HEAD", url, **kw)

    def delete(self, url, **kw):  # noqa: ANN001, ANN201
        return self._handle("DELETE", url, **kw)
