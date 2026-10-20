"""Text-to-speech, one synthesis per scene/beat so that timing is exact.

Providers (TTS_PROVIDER / media.tts_provider = auto | elevenlabs | google | azure | edge | none):
  elevenlabs - most natural English voices (paid, tiny plan). Secrets ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID
               (optional ELEVENLABS_MODEL, default eleven_multilingual_v2; eleven_flash_v2_5 costs half).
  google     - Google Cloud Text-to-Speech. Default voice en-IN-Chirp3-HD-Charon (Indian English, near-human);
               first 1 million characters/month free. Secret GOOGLE_TTS_API_KEY (optional GOOGLE_TTS_VOICE).
  azure      - Azure AI Speech REST API (AZURE_SPEECH_KEY, AZURE_SPEECH_REGION).
  edge       - edge-tts Python package (free, unofficial; clearly synthetic).
  none       - silent.
"Premium" providers (elevenlabs, google) are the only ones used for the kinetic voice-over unless voice.allow_basic.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

import requests

from ..settings import Settings, env
from .pronounce import load_words, speakable
from .util import MediaError, probe_duration

log = logging.getLogger(__name__)
WPM = 165.0
PREMIUM = ("elevenlabs", "google")
ORDER = ("elevenlabs", "google", "azure", "edge")


@dataclass
class BeatAudio:
    path: Path | None
    duration: float
    words: list[tuple[str, float, float]] = field(default_factory=list)
    provider: str = "none"


def estimate_words(text: str, duration: float) -> list[tuple[str, float, float]]:
    words = text.split()
    if not words:
        return []
    weights = [len(w) + 2 for w in words]
    total = float(sum(weights))
    out, cursor = [], 0.0
    for word, weight in zip(words, weights):
        span = duration * weight / total
        out.append((word, cursor, cursor + span))
        cursor += span
    return out


def silent_duration(text: str) -> float:
    return max(1.5, len(text.split()) / WPM * 60.0 + 0.3)


class TTS:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.words = load_words(settings.root)
        self.provider = self._resolve()
        self.used: set[str] = set()

    def _resolve(self) -> str:
        choice = (env("TTS_PROVIDER") or str(self.settings.get("media.tts_provider", "auto"))).lower()
        if choice != "auto":
            return choice
        for name in ORDER:
            if self._configured(name):
                return name
        return "none"

    @staticmethod
    def _configured(provider: str) -> bool:
        if provider == "elevenlabs":
            return bool(env("ELEVENLABS_API_KEY") and env("ELEVENLABS_VOICE_ID"))
        if provider == "google":
            return bool(env("GOOGLE_TTS_API_KEY"))
        if provider == "azure":
            return bool(env("AZURE_SPEECH_KEY") and env("AZURE_SPEECH_REGION"))
        if provider == "edge":
            try:
                import edge_tts  # noqa: F401

                return True
            except ImportError:
                return False
        return False

    def chain(self, premium_only: bool = False) -> list[str]:
        if self.provider in ("none", ""):
            return []
        order = [self.provider]
        for fallback in ORDER:
            if fallback not in order:
                order.append(fallback)
        order = [p for p in order if self._configured(p)]
        if premium_only:
            order = [p for p in order if p in PREMIUM]
        return order

    @property
    def voice(self) -> str:
        lang = self.settings.get("brand.language", "en")
        return str(self.settings.get("media.voice_hi" if lang == "hi" else "media.voice_en", "en-IN-NeerjaNeural"))

    def synthesize(self, text: str, out_path: Path, previous: str | None = None, next_: str | None = None,
                   premium_only: bool = False) -> BeatAudio:
        original = re.sub(r"\s+", " ", text).strip()
        spoken = speakable(original, self.words)
        prev = speakable(previous, self.words) if previous else None
        nxt = speakable(next_, self.words) if next_ else None
        for provider in self.chain(premium_only):
            try:
                audio = getattr(self, f"_{provider}")(spoken, out_path, prev, nxt)
                if spoken.split() != original.split():
                    audio.words = estimate_words(original, audio.duration)   # captions show the written form
                self.used.add(provider)
                return audio
            except Exception as exc:  # noqa: BLE001
                log.warning("TTS provider %s failed: %s", provider, exc)
        if premium_only or not self.settings.get("media.allow_silent", True):
            if premium_only:
                return BeatAudio(None, 0.0, [], "none")
            raise MediaError("no TTS provider available and media.allow_silent is false")
        duration = silent_duration(original)
        return BeatAudio(None, duration, estimate_words(original, duration), "none")

    # ----------------------------------------------------------- elevenlabs
    def _elevenlabs(self, text: str, out_path: Path, previous: str | None = None, next_: str | None = None) -> BeatAudio:
        key, voice = env("ELEVENLABS_API_KEY"), env("ELEVENLABS_VOICE_ID")
        model = env("ELEVENLABS_MODEL") or str(self.settings.get("voice.elevenlabs_model", "eleven_multilingual_v2"))
        settings = {"stability": float(self.settings.get("voice.stability", 0.5)),
                    "similarity_boost": float(self.settings.get("voice.similarity", 0.8)),
                    "style": float(self.settings.get("voice.style", 0.2)), "use_speaker_boost": True}
        speed = float(self.settings.get("voice.speed", 1.0))
        if abs(speed - 1.0) > 0.01:
            settings["speed"] = speed
        body: dict = {"text": text, "model_id": model, "voice_settings": settings}
        if previous:
            body["previous_text"] = previous
        if next_:
            body["next_text"] = next_
        response = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
                                 params={"output_format": "mp3_44100_128"},
                                 headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"},
                                 json=body, timeout=120)
        if response.status_code != 200:
            raise MediaError(f"ElevenLabs HTTP {response.status_code}: {response.text[:200]}")
        out_path.write_bytes(response.content)
        duration = probe_duration(out_path)
        return BeatAudio(out_path, duration, estimate_words(text, duration), "elevenlabs")

    # --------------------------------------------------------------- google
    def _google(self, text: str, out_path: Path, previous: str | None = None, next_: str | None = None) -> BeatAudio:
        key = env("GOOGLE_TTS_API_KEY")
        voice = env("GOOGLE_TTS_VOICE") or str(self.settings.get("media.google_voice", "en-IN-Chirp3-HD-Charon"))
        hd = "chirp" in voice.lower()
        audio_config = ({"audioEncoding": "LINEAR16", "sampleRateHertz": 24000} if hd
                        else {"audioEncoding": "MP3", "speakingRate": 1.05})
        body = {"input": {"text": text}, "voice": {"languageCode": voice[:5], "name": voice},
                "audioConfig": audio_config}
        response = requests.post("https://texttospeech.googleapis.com/v1/text:synthesize",
                                 params={"key": key}, json=body, timeout=90)
        if response.status_code != 200:
            raise MediaError(f"Google TTS HTTP {response.status_code}: {response.text[:200]}")
        target = out_path.with_suffix(".wav") if hd else out_path
        target.write_bytes(base64.b64decode(response.json()["audioContent"]))
        duration = probe_duration(target)
        return BeatAudio(target, duration, estimate_words(text, duration), "google")

    # ---------------------------------------------------------------- azure
    def _azure(self, text: str, out_path: Path, previous: str | None = None, next_: str | None = None) -> BeatAudio:
        region = env("AZURE_SPEECH_REGION")
        key = env("AZURE_SPEECH_KEY")
        rate = self.settings.get("media.speech_rate", "+5%")
        lang = "hi-IN" if self.settings.get("brand.language") == "hi" else "en-IN"
        ssml = (f"<speak version='1.0' xml:lang='{lang}'><voice name='{self.voice}'>"
                f"<prosody rate='{rate}'>{escape(text)}</prosody></voice></speak>")
        response = requests.post(
            f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
            headers={"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/ssml+xml",
                     "X-Microsoft-OutputFormat": "audio-24khz-96kbitrate-mono-mp3", "User-Agent": "newsroom-bot"},
            data=ssml.encode("utf-8"), timeout=60)
        if response.status_code != 200:
            raise MediaError(f"Azure TTS HTTP {response.status_code}: {response.text[:200]}")
        out_path.write_bytes(response.content)
        duration = probe_duration(out_path)
        return BeatAudio(out_path, duration, estimate_words(text, duration), "azure")

    # ----------------------------------------------------------------- edge
    def _edge(self, text: str, out_path: Path, previous: str | None = None, next_: str | None = None) -> BeatAudio:
        import edge_tts

        rate = str(self.settings.get("media.speech_rate", "+5%"))

        async def go() -> list[tuple[str, float, float]]:
            try:
                communicate = edge_tts.Communicate(text, self.voice, rate=rate, boundary="WordBoundary")
            except TypeError:
                communicate = edge_tts.Communicate(text, self.voice, rate=rate)
            audio = bytearray()
            words: list[tuple[str, float, float]] = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio.extend(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    start = chunk["offset"] / 10_000_000.0
                    words.append((chunk["text"], start, start + chunk["duration"] / 10_000_000.0))
            out_path.write_bytes(bytes(audio))
            return words

        words = asyncio.run(go())
        duration = probe_duration(out_path)
        if not words:
            words = estimate_words(text, duration)
        return BeatAudio(out_path, duration, words, "edge")


def voice_status(settings: Settings) -> tuple[bool, str]:
    """Is a voice-over going to be used for kinetic Reels, and with which provider?
    voice.mode: off | auto (only with a premium voice configured) | on (use whatever voice is available)."""
    mode = str(settings.get("voice.mode", "auto")).lower()
    if mode == "off":
        return False, "voice.mode is off"
    tts = TTS(settings)
    premium = tts.chain(premium_only=True)
    if premium:
        return True, premium[0]
    basic = tts.chain()
    if basic and (mode == "on" or settings.get("voice.allow_basic", False)):
        return True, basic[0]
    return False, "no premium voice configured (set ELEVENLABS_API_KEY + ELEVENLABS_VOICE_ID, or GOOGLE_TTS_API_KEY)"
