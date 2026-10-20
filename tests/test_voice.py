import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from newsroom.content import ContentRejected
from newsroom.gates import UpstreamTools
from newsroom.kinetic_content import KineticContentFactory
from newsroom.media import kinetic as kinetic_module
from newsroom.media.kinetic import build_timeline, render_kinetic
from newsroom.media.pronounce import speakable
from newsroom.media.tts import TTS, BeatAudio, estimate_words, voice_status
from newsroom.media.util import have_ffmpeg, probe, run
from newsroom.settings import load_settings
from newsroom.store import Store
from tests.helpers import ROOT

EL_ENV = {"ELEVENLABS_API_KEY": "KEY", "ELEVENLABS_VOICE_ID": "VOICE123"}
GO_ENV = {"GOOGLE_TTS_API_KEY": "GKEY"}
ALL_VOICE_VARS = ["ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "ELEVENLABS_MODEL", "GOOGLE_TTS_API_KEY",
                  "GOOGLE_TTS_VOICE", "AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION", "TTS_PROVIDER"]


def settings(**cfg):
    voice = {"mode": "auto", **cfg.pop("voice", {})}          # tests are explicit about the voice mode
    return load_settings(root=ROOT, db_path=":memory:", offline=True,
                         cfg={"kinetic": {"fps": 10}, "qa": {"min_fps": 8}, "voice": voice, **cfg})


def clean_env(extra=None):
    env = {k: v for k, v in os.environ.items() if k not in ALL_VOICE_VARS}
    env.update(extra or {})
    return mock.patch.dict(os.environ, env, clear=True)


def tone(path: Path, seconds: float) -> bytes:
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=f=220:d={seconds}", "-ac", "1",
         "-ar", "24000", str(path)])
    return path.read_bytes()


class Resp:
    def __init__(self, status=200, content=b"", payload=None, text=""):
        self.status_code, self.content, self._payload, self.text = status, content, payload, text

    def json(self):
        return self._payload


class PronounceTests(unittest.TestCase):
    def test_indian_news_terms(self):
        out = speakable("RBI says UPI hit \u20b92,000 crore, up 31% on Sunday. IndiGo 6E 6115 raised $5 bn, Rs 500.")
        self.assertIn("R B I", out)
        self.assertIn("U P I", out)
        self.assertIn("2,000 crore rupees", out)
        self.assertIn("31 percent", out)
        self.assertIn("six E", out)
        self.assertIn("5 billion dollars", out)
        self.assertIn("500 rupees", out)

    def test_plain_words_are_untouched(self):
        self.assertEqual(speakable("The crew took control."), "The crew took control.")


@unittest.skipUnless(have_ffmpeg(), "ffmpeg not installed")
class ProviderTests(unittest.TestCase):
    def test_elevenlabs_request_matches_the_documented_api(self):
        with tempfile.TemporaryDirectory() as tmp, clean_env(EL_ENV):
            data = tone(Path(tmp) / "t.mp3", 1.2)
            calls = []

            def fake_post(url, **kw):
                calls.append((url, kw))
                return Resp(200, data)

            with mock.patch("newsroom.media.tts.requests.post", side_effect=fake_post):
                audio = TTS(settings()).synthesize("RBI says UPI is up 31%.", Path(tmp) / "a.mp3",
                                                   previous="Before.", next_="After.")
            url, kw = calls[0]
            self.assertEqual(url, "https://api.elevenlabs.io/v1/text-to-speech/VOICE123")
            self.assertEqual(kw["params"], {"output_format": "mp3_44100_128"})
            self.assertEqual(kw["headers"]["xi-api-key"], "KEY")
            body = kw["json"]
            self.assertEqual(body["model_id"], "eleven_multilingual_v2")
            self.assertIn("R B I", body["text"])
            self.assertEqual(body["previous_text"], "Before.")
            self.assertEqual(body["next_text"], "After.")
            self.assertEqual(audio.provider, "elevenlabs")
            self.assertAlmostEqual(audio.duration, 1.2, delta=0.2)

    def test_google_chirp3_hd_request_and_wav_output(self):
        with tempfile.TemporaryDirectory() as tmp, clean_env(GO_ENV):
            wav = Path(tmp) / "t.wav"
            data = tone(wav, 1.0)
            seen = {}

            def fake_post(url, **kw):
                seen.update(url=url, **kw)
                return Resp(200, payload={"audioContent": base64.b64encode(data).decode()})

            with mock.patch("newsroom.media.tts.requests.post", side_effect=fake_post):
                audio = TTS(settings()).synthesize("Hello there.", Path(tmp) / "a.mp3")
            self.assertEqual(seen["json"]["voice"], {"languageCode": "en-IN", "name": "en-IN-Chirp3-HD-Charon"})
            self.assertEqual(seen["json"]["audioConfig"]["audioEncoding"], "LINEAR16")
            self.assertNotIn("speakingRate", seen["json"]["audioConfig"])
            self.assertEqual(audio.path.suffix, ".wav")
            self.assertEqual(audio.provider, "google")

    def test_failure_falls_through_and_premium_only_never_goes_robotic(self):
        with tempfile.TemporaryDirectory() as tmp, clean_env({**EL_ENV, **GO_ENV}):
            with mock.patch("newsroom.media.tts.requests.post", return_value=Resp(500, text="boom")):
                audio = TTS(settings()).synthesize("Hello.", Path(tmp) / "a.mp3", premium_only=True)
            self.assertIsNone(audio.path)

    def test_voice_status_rules(self):
        with clean_env():
            self.assertFalse(voice_status(settings())[0])
        with clean_env(EL_ENV):
            self.assertEqual(voice_status(settings()), (True, "elevenlabs"))
        with clean_env(GO_ENV):
            self.assertEqual(voice_status(settings()), (True, "google"))
        with clean_env(EL_ENV):
            self.assertFalse(voice_status(settings(voice={"mode": "off"}))[0])
        with clean_env({"AZURE_SPEECH_KEY": "k", "AZURE_SPEECH_REGION": "r"}):
            self.assertFalse(voice_status(settings())[0])                       # basic voices are not enough
            self.assertTrue(voice_status(settings(voice={"allow_basic": True}))[0])

    def test_default_mode_uses_the_best_available_voice_even_a_free_one(self):
        default = load_settings(root=ROOT, db_path=":memory:", offline=True)
        with clean_env({"AZURE_SPEECH_KEY": "k", "AZURE_SPEECH_REGION": "r"}):
            self.assertEqual(voice_status(default), (True, "azure"))
        with clean_env({**EL_ENV, "AZURE_SPEECH_KEY": "k", "AZURE_SPEECH_REGION": "r"}):
            self.assertEqual(voice_status(default), (True, "elevenlabs"))      # premium still wins
        with clean_env({"TTS_PROVIDER": "none", **EL_ENV}):
            self.assertFalse(voice_status(default)[0])                         # explicit none is respected

    def test_captions_keep_the_written_form(self):
        words = estimate_words("UPI is up", 1.0)
        self.assertEqual([w for w, _, _ in words], ["UPI", "is", "up"])


class TimelineWithVoiceTests(unittest.TestCase):
    RAW = [{"kind": "hook", "lines": ["A", "B"]}, {"kind": "detail", "lines": ["C", "D"]},
           {"kind": "closer", "lines": ["Q?"]}]

    def test_scenes_last_as_long_as_their_narration_needs(self):
        auds = [3.9, 1.0, 5.2]
        tl = build_timeline(self.RAW, 108, 60.0, auds)
        half = 60 / 108 / 2
        for sc, a in zip(tl, auds):
            self.assertGreaterEqual(sc.dur, a + sc.lead + 0.29)
            self.assertAlmostEqual(sc.dur / half, round(sc.dur / half), places=6)
        self.assertEqual([sc.src for sc in tl], [0, 1, 2])


def fake_synth_factory(tmp: Path):
    def fake(self, text, out_path, previous=None, next_=None, premium_only=False):
        seconds = max(1.0, len(text.split()) / 2.8)
        tone(Path(out_path), seconds)
        self.used.add("elevenlabs")
        return BeatAudio(Path(out_path), seconds, [], "elevenlabs")
    return fake


@unittest.skipUnless(have_ffmpeg(), "ffmpeg not installed")
class VoicedRenderTests(unittest.TestCase):
    RAW = [
        {"kind": "hook", "lines": ["A POWER BANK", "TURNED", "A FLIGHT BACK"], "accent": 1,
         "say": "A power bank forced an IndiGo flight back."},
        {"kind": "alert", "lines": ["SMOKE."], "sub": "from a device", "graphic": "pulse",
         "say": "Smoke came from a device in the cabin."},
        {"kind": "closer", "lines": ["DO YOU CARRY", "A POWER BANK?"], "sub": "Tell me below.",
         "say": "Do you carry a power bank when you fly?"}]

    def test_voiced_reel_has_speech_and_is_longer_than_text_only(self):
        with tempfile.TemporaryDirectory() as tmp, clean_env(EL_ENV):
            with mock.patch.object(TTS, "synthesize", fake_synth_factory(Path(tmp))):
                voiced = render_kinetic(self.RAW, "Source: Test", Path(tmp) / "v", settings(), "sV")
            self.assertTrue(voiced.voiced)
            self.assertEqual(voiced.voice_provider, "elevenlabs")
            streams = {s["codec_type"] for s in probe(voiced.video)["streams"]}
            self.assertEqual(streams, {"video", "audio"})
        with tempfile.TemporaryDirectory() as tmp, clean_env():
            plain = render_kinetic(self.RAW, "Source: Test", Path(tmp) / "p", settings(), "sV")
            self.assertFalse(plain.voiced)
        self.assertGreater(voiced.duration, plain.duration)

    def test_provider_failure_degrades_to_text_only_instead_of_breaking(self):
        def broken(self, text, out_path, previous=None, next_=None, premium_only=False):
            return BeatAudio(None, 0.0, [], "none")

        with tempfile.TemporaryDirectory() as tmp, clean_env(EL_ENV):
            with mock.patch.object(TTS, "synthesize", broken):
                result = render_kinetic(self.RAW, "Source: Test", Path(tmp), settings(), "sF")
            self.assertFalse(result.voiced)
            self.assertTrue(result.video.exists())


class VoiceContentTests(unittest.TestCase):
    FACTS = [{"text": "IndiGo flight 6E 6115 was diverted to Hyderabad on Sunday after smoke from a device", "evidence": "e"},
             {"text": "The flight landed safely and the crew controlled the situation", "evidence": "e"},
             {"text": "Passengers continued to Chennai on another aircraft", "evidence": "e"}]
    STORY = {"id": "s1", "title": "IndiGo flight diverted to Hyderabad after smoke from a device", "pillar": "tech",
             "facts": FACTS, "sources": [{"source": "NDTV", "domain": "ndtv.com", "tier": 2}], "verification": {}}

    def good(self):
        return {
            "hook_candidates": [{"formula_id": 23, "lines": ["A POWER BANK", "TURNED", "A FLIGHT BACK"], "accent": 1,
                                 "say": "A power bank forced an IndiGo flight back."}],
            "scenes": [
                {"kind": "detail", "lines": ["INDIGO 6E 6115", "SUNDAY"], "graphic": "route", "label": "Hyderabad",
                 "say": "Flight 6E 6115 was diverted to Hyderabad on Sunday."},
                {"kind": "alert", "lines": ["SMOKE."], "sub": "from a device", "graphic": "smoke",
                 "say": "Smoke from a device was the reason."},
                {"kind": "detail", "lines": ["THE CREW", "TOOK CONTROL."], "accent": 1,
                 "say": "The crew took control."},
                {"kind": "relief", "lines": ["LANDED SAFELY."], "graphic": "check",
                 "say": "It landed safely in Hyderabad."},
                {"kind": "twist", "lines": ["THEN ON TO", "CHENNAI"], "sub": "on another plane", "accent": 1,
                 "say": "Passengers then flew on to Chennai."}],
            "closer": {"lines": ["DO YOU CARRY", "A POWER BANK", "ON FLIGHTS?"], "sub": "Tell me below.",
                       "say": "Do you carry a power bank when you fly?"}}

    def factory(self, raw):
        class Fake:
            name = "fake"

            def json(self, *a, **k):
                return raw

        return KineticContentFactory(settings(), Fake(), UpstreamTools(Path(tempfile.mkdtemp())), Store(":memory:"))

    def test_voiced_script_passes_and_discloses_the_ai_voice(self):
        with clean_env(EL_ENV):
            package = self.factory(self.good()).build(dict(self.STORY), True)
        self.assertTrue(package["voice"])
        self.assertTrue(all(s.get("say") for s in package["scenes"]))
        self.assertIn("AI-generated voice", package["caption"])
        self.assertIn("structure", [g["name"] for g in package["gates"]])

    def test_missing_narration_is_rejected_when_voice_is_on(self):
        raw = self.good()
        del raw["scenes"][1]["say"]
        with clean_env(EL_ENV), self.assertRaises(ContentRejected) as ctx:
            self.factory(raw).build(dict(self.STORY), True)
        self.assertIn("say", str(ctx.exception))

    def test_invented_number_in_narration_is_rejected(self):
        raw = self.good()
        raw["scenes"][2]["say"] = "The crew took control of 187 passengers."
        with clean_env(EL_ENV), self.assertRaises(ContentRejected) as ctx:
            self.factory(raw).build(dict(self.STORY), True)
        self.assertIn("187", str(ctx.exception))

    def test_without_a_voice_no_narration_is_required_and_no_disclosure(self):
        raw = self.good()
        for s in raw["scenes"]:
            s.pop("say")
        with clean_env():
            package = self.factory(raw).build(dict(self.STORY), False)
        self.assertFalse(package["voice"])
        self.assertNotIn("AI-generated voice", package["caption"])


@unittest.skipUnless(have_ffmpeg(), "ffmpeg not installed")
class VoiceTestCommandTests(unittest.TestCase):
    def test_voice_test_writes_a_sample_for_each_provider(self):
        from newsroom import cli
        with tempfile.TemporaryDirectory() as tmp, clean_env({**EL_ENV, "NEWSROOM_OUT": f"{tmp}/out",
                                                              "NEWSROOM_DB": f"{tmp}/db", "NEWSROOM_LOGS": f"{tmp}/l",
                                                              "NEWSROOM_OFFLINE": "1"}):
            def fake(self, text, out_path, previous=None, next_=None):
                tone(Path(out_path), 2.0)
                return BeatAudio(Path(out_path), 2.0, [], "elevenlabs")

            with mock.patch.object(TTS, "_elevenlabs", fake):
                self.assertEqual(cli.main(["voice-test", "--provider", "elevenlabs"]), 0)
            self.assertTrue((Path(tmp) / "out" / "voice_test" / "voice_test_elevenlabs.mp3").exists())


if __name__ == "__main__":
    unittest.main()
