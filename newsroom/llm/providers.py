"""LLM providers over plain HTTPS (no vendor SDKs). All return parsed JSON."""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import requests

from ..settings import env

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


def extract_json(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise LLMError("model did not return a JSON object")
    try:
        data = json.loads(cleaned[start:end + 1])
    except ValueError as exc:
        raise LLMError(f"invalid JSON from model: {exc}") from exc
    if not isinstance(data, dict):
        raise LLMError("model JSON is not an object")
    return data


class HTTPProvider:
    name = "http"
    default_model = ""

    def __init__(self, api_key: str, model: str | None = None, timeout: float = 120.0, retries: int = 4) -> None:
        self.api_key = api_key
        self._model = model or self.default_model
        self.timeout = timeout
        self.retries = retries

    @property
    def model(self) -> str:
        return self._model

    def _request(self, system: str, prompt: str, temperature: float, max_tokens: int) -> tuple[str, dict, dict]:
        raise NotImplementedError

    def _extract_text(self, data: dict) -> str:
        raise NotImplementedError

    def _on_bad_request(self, response) -> bool:  # noqa: ANN001
        """Return True if the provider adjusted itself and the call should be retried immediately."""
        return False

    def _send(self, system: str, prompt: str, temperature: float, max_tokens: int) -> str:
        last = ""
        attempt = 0
        adjusted = False
        while attempt < self.retries:
            url, headers, body = self._request(system, prompt, temperature, max_tokens)
            try:
                response = requests.post(url, headers=headers, json=body, timeout=self.timeout)
            except requests.RequestException as exc:
                last = repr(exc)
                time.sleep(min(2 ** attempt, 15))
                attempt += 1
                continue
            if response.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {response.status_code}: {response.text[:200]}"
                wait = min(2 ** (attempt + 1), 20)
                retry_after = response.headers.get("retry-after", "")
                if retry_after.replace(".", "", 1).isdigit():
                    wait = min(float(retry_after) + 1, 45)
                log.warning("%s: %s (attempt %d); waiting %.0fs", self.name, last[:120], attempt + 1, wait)
                time.sleep(wait)
                attempt += 1
                continue
            if response.status_code == 400 and not adjusted and self._on_bad_request(response):
                adjusted = True
                continue
            if response.status_code != 200:
                raise LLMError(f"{self.name} HTTP {response.status_code}: {response.text[:300]}")
            return self._extract_text(response.json())
        raise LLMError(f"{self.name} failed after {self.retries} attempts: {last}")

    def json(self, task: str, system: str, prompt: str, payload: dict | None = None,
             temperature: float = 0.3, max_tokens: int = 1800) -> dict[str, Any]:
        text = self._send(system, prompt, temperature, max_tokens)
        try:
            return extract_json(text)
        except LLMError:
            log.warning("%s returned unparseable JSON for task %s; retrying once", self.name, task)
            text = self._send(system, prompt + "\n\nReturn ONLY a valid JSON object, nothing else.",
                              max(temperature - 0.1, 0.0), max_tokens)
            return extract_json(text)


class GeminiProvider(HTTPProvider):
    name = "gemini"
    default_model = "gemini-2.5-flash"

    def _request(self, system, prompt, temperature, max_tokens):  # noqa: ANN001
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        headers = {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max(max_tokens * 4, 4096),
                                 "responseMimeType": "application/json"},
        }
        return url, headers, body

    def _extract_text(self, data):  # noqa: ANN001
        try:
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected Gemini response: {str(data)[:300]}") from exc


class AnthropicProvider(HTTPProvider):
    name = "anthropic"
    default_model = "claude-haiku-4-5-20251001"

    def _request(self, system, prompt, temperature, max_tokens):  # noqa: ANN001
        url = "https://api.anthropic.com/v1/messages"
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        body = {"model": self.model, "max_tokens": max_tokens, "temperature": temperature, "system": system,
                "messages": [{"role": "user", "content": prompt}]}
        return url, headers, body

    def _extract_text(self, data):  # noqa: ANN001
        try:
            return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        except (KeyError, TypeError) as exc:
            raise LLMError(f"unexpected Anthropic response: {str(data)[:300]}") from exc


# Models we prefer, best first. Used only when LLM_MODEL is not set.
GROQ_PREFERRED = [
    "openai/gpt-oss-120b", "llama-3.3-70b-versatile", "openai/gpt-oss-20b", "qwen/qwen3-32b",
    "llama-3.1-8b-instant",
]
NOT_CHAT = re.compile(r"whisper|tts|guard|embed|orpheus|playai|compound|safeguard|vision|distil|prompt", re.I)


class OpenAICompatProvider(HTTPProvider):
    """Groq, OpenAI and any OpenAI-compatible /chat/completions endpoint.

    If no model is given, asks the provider's own GET /models list which models are live and picks the best one,
    so a retired model name can never break the pipeline again.
    """

    name = "openai_compat"

    def __init__(self, api_key: str, base_url: str, model: str | None = None,
                 preferred: list[str] | None = None, **kw: Any) -> None:
        super().__init__(api_key, model, **kw)
        self.base_url = base_url.rstrip("/")
        self.preferred = preferred or []
        self.json_mode = True
        self._resolved: str | None = None

    @property
    def model(self) -> str:
        if self._model:
            return self._model
        if self._resolved is None:
            self._resolved = self._discover()
        return self._resolved

    def _discover(self) -> str:
        try:
            response = requests.get(f"{self.base_url}/models", headers={"Authorization": f"Bearer {self.api_key}"},
                                    timeout=30)
            response.raise_for_status()
            ids = [m["id"] for m in response.json().get("data", []) if m.get("id")]
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"could not list models from {self.base_url}: {exc}") from exc
        for wanted in self.preferred:
            if wanted in ids:
                log.info("%s: using model %s", self.name, wanted)
                return wanted
        usable = [i for i in ids if not NOT_CHAT.search(i)]
        if not usable:
            raise LLMError(f"no usable chat model in provider list: {ids[:10]}")
        log.info("%s: preferred models not available; using %s", self.name, usable[0])
        return usable[0]

    def _request(self, system, prompt, temperature, max_tokens):  # noqa: ANN001
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        body: dict[str, Any] = {"model": self.model, "temperature": temperature,
                                "max_tokens": min(max(max_tokens * 3, 4096), 8192),
                                "messages": [{"role": "system", "content": system},
                                             {"role": "user", "content": prompt}]}
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        return url, headers, body

    def _on_bad_request(self, response) -> bool:  # noqa: ANN001
        text = response.text.lower()
        if self.json_mode and ("response_format" in text or "json" in text):
            log.warning("%s rejected JSON mode; retrying without it", self.name)
            self.json_mode = False
            return True
        return False

    def _extract_text(self, data):  # noqa: ANN001
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected chat-completions response: {str(data)[:300]}") from exc


def get_llm(provider: str | None = None):  # noqa: ANN201
    """Pick a provider. LLM_PROVIDER = auto|gemini|anthropic|groq|openai|openai_compat|offline."""
    from .offline import OfflineLLM

    choice = (provider or env("LLM_PROVIDER", "auto") or "auto").lower()
    model = env("LLM_MODEL")
    if choice == "offline":
        return OfflineLLM()
    if choice in ("auto", "gemini") and env("GEMINI_API_KEY"):
        return GeminiProvider(env("GEMINI_API_KEY"), model)
    if choice in ("auto", "anthropic") and env("ANTHROPIC_API_KEY"):
        return AnthropicProvider(env("ANTHROPIC_API_KEY"), model)
    if choice in ("auto", "groq") and env("GROQ_API_KEY"):
        return OpenAICompatProvider(env("GROQ_API_KEY"), "https://api.groq.com/openai/v1", model,
                                    preferred=GROQ_PREFERRED)
    if choice in ("auto", "openai") and env("OPENAI_API_KEY"):
        return OpenAICompatProvider(env("OPENAI_API_KEY"), "https://api.openai.com/v1", model or "gpt-4o-mini")
    if choice == "openai_compat" and env("LLM_API_KEY") and env("LLM_BASE_URL"):
        return OpenAICompatProvider(env("LLM_API_KEY"), env("LLM_BASE_URL"), model)
    if choice != "auto":
        log.warning("LLM_PROVIDER=%s requested but its API key is missing; using offline provider", choice)
    else:
        log.warning("no LLM API key found (GEMINI_API_KEY / ANTHROPIC_API_KEY / GROQ_API_KEY / OPENAI_API_KEY); "
                    "using the extractive offline provider")
    return OfflineLLM()
