"""Telegram Bot API: preview + one-tap approval (Approve / Reject inline buttons)."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..net import Http
from ..settings import env
from ..store import Store

log = logging.getLogger(__name__)


class TelegramClient:
    def __init__(self, token: str, chat_id: str, http: Http) -> None:
        self.token = token
        self.chat_id = str(chat_id)
        self.http = http

    @classmethod
    def from_env(cls, http: Http) -> "TelegramClient | None":
        token, chat = env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID")
        if token and chat:
            return cls(token, chat, http)
        return None

    def _call(self, method: str, **kwargs: Any) -> dict[str, Any]:
        response = self.http.post(f"https://api.telegram.org/bot{self.token}/{method}", timeout=120, **kwargs)
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {str(data)[:200]}")
        return data

    def send_message(self, text: str) -> None:
        self._call("sendMessage", data={"chat_id": self.chat_id, "text": text[:4000]})

    def send_audio(self, audio: Path, caption: str) -> None:
        with audio.open("rb") as handle:
            self._call("sendAudio", data={"chat_id": self.chat_id, "caption": caption[:1000]}, files={"audio": handle})

    def send_preview(self, story_id: str, video: Path, text: str) -> None:
        keyboard = {"inline_keyboard": [[{"text": "Approve", "callback_data": f"a:{story_id}"},
                                         {"text": "Reject", "callback_data": f"r:{story_id}"}]]}
        with video.open("rb") as handle:
            self._call("sendVideo", data={"chat_id": self.chat_id, "caption": text[:1000],
                                          "reply_markup": json.dumps(keyboard), "supports_streaming": "true"},
                       files={"video": handle})

    def poll_decisions(self, store: Store) -> list[tuple[str, str]]:
        """Read button presses since the last poll. Returns [(story_id, 'approve'|'reject')]."""
        offset = int(store.kv_get("tg_offset", "0") or 0)
        data = self._call("getUpdates", data={"offset": offset, "timeout": 0,
                                              "allowed_updates": json.dumps(["callback_query"])})
        decisions: list[tuple[str, str]] = []
        max_update = offset - 1
        for update in data.get("result", []):
            max_update = max(max_update, int(update["update_id"]))
            query = update.get("callback_query")
            if not query:
                continue
            chat = str(query.get("message", {}).get("chat", {}).get("id", ""))
            if chat != self.chat_id:
                log.warning("ignoring callback from unexpected chat %s", chat)
                continue
            action, _, story_id = str(query.get("data", "")).partition(":")
            if action not in ("a", "r") or not story_id:
                continue
            decision = "approve" if action == "a" else "reject"
            decisions.append((story_id, decision))
            try:
                self._call("answerCallbackQuery", data={"callback_query_id": query["id"], "text": decision + "d"})
                message = query.get("message", {})
                self._call("editMessageReplyMarkup", data={"chat_id": self.chat_id,
                                                            "message_id": message.get("message_id"),
                                                            "reply_markup": json.dumps({"inline_keyboard": []})})
            except RuntimeError as exc:
                log.debug("telegram ack failed: %s", exc)
        store.kv_set("tg_offset", str(max_update + 1))
        return decisions
