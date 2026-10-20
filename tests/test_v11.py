import tempfile
import unittest
from pathlib import Path

from newsroom.content import mascot_allowed, normalize_script
from newsroom.llm.providers import LLMError
from newsroom.media import broll
from newsroom.media.audio import make_pad, mix_audio
from newsroom.media.icons import ICON_NAMES, MOODS, icon_image, mascot_image, pick_icon
from newsroom.media.subtitles import build_ass
from newsroom.media.util import have_ffmpeg, probe, probe_duration, run
from newsroom.models import Fact
from newsroom.net import Http
from newsroom.settings import load_settings
from newsroom.store import iso, utcnow
from newsroom.verify import verify_story
from tests.helpers import ROOT, FakeHttp, FakeResponse

SETTINGS = load_settings(root=ROOT, db_path=":memory:", offline=True, cfg={"media": {"tts_provider": "none"}})


class IconTests(unittest.TestCase):
    def test_keyword_icon_choice(self):
        self.assertEqual(pick_icon("IndiGo flight diverted after power bank smoke"), "battery")
        self.assertEqual(pick_icon("RBI changes UPI rule"), "card")
        self.assertEqual(pick_icon("OpenAI launches a new model"), "chip")
        self.assertEqual(pick_icon("nothing special"), "spark")
        self.assertEqual(pick_icon("anything", "rocket"), "rocket")

    def test_every_icon_and_mood_draws(self):
        for name in ICON_NAMES:
            self.assertEqual(icon_image(name, 64, (255, 255, 255), (255, 184, 0), None).size, (64, 64))
        for mood in MOODS:
            if mood != "none":
                self.assertEqual(mascot_image(mood, 64, (255, 184, 0)).size, (64, 64))


class MascotPolicyTests(unittest.TestCase):
    def story(self, **ver):
        return {"id": "s1", "verification": ver}

    def test_never_for_sensitive_or_flagged_stories(self):
        s = SETTINGS
        for ver in ({"meme_blocked": True}, {"needs_review": True}, {"sensitivity": "high"}):
            self.assertFalse(mascot_allowed(self.story(**ver), s))

    def test_probability_extremes_and_switch(self):
        always = load_settings(root=ROOT, db_path=":memory:", offline=True, cfg={"mascot": {"probability": 1.0}})
        never = load_settings(root=ROOT, db_path=":memory:", offline=True, cfg={"mascot": {"probability": 0.0}})
        off = load_settings(root=ROOT, db_path=":memory:", offline=True, cfg={"mascot": {"enabled": False, "probability": 1.0}})
        self.assertTrue(mascot_allowed(self.story(), always))
        self.assertFalse(mascot_allowed(self.story(), never))
        self.assertFalse(mascot_allowed(self.story(), off))

    def test_invalid_mood_and_icon_are_sanitised(self):
        raw = {"hook_candidates": [{"spoken": "Hello 20", "mood": "angry"}], "context": {"say": "a", "icon": "dragon"},
               "main": [{"say": "b"}, {"say": "c"}], "twist": {"say": "d"}, "cta": {"say": "e"}}
        script = normalize_script(raw)
        self.assertEqual(script["hook_candidates"][0]["mood"], "none")
        self.assertEqual(script["context"]["icon"], "")


class VerifyMemeTests(unittest.TestCase):
    def test_harm_words_block_memes_but_not_the_story(self):
        facts = [Fact(f"fact {i} 20", "e", "u") for i in range(3)]
        src = lambda d: {"domain": d, "tier": 2, "published_at": iso(utcnow()), "title": "x", "url": f"https://{d}/x",
                         "source": d, "origin": "rss", "fetchable": True, "summary": "", "extra": {}}
        story = {"title": "Flight diverted after smoke from a device", "sources": [src("a.com"), src("b.com")]}
        result = verify_story(story, facts, SETTINGS)
        self.assertTrue(result.passed)
        self.assertTrue(result.meme_blocked)
        calm = {"title": "Startup raises funding", "sources": [src("a.com"), src("b.com")]}
        self.assertFalse(verify_story(calm, facts, SETTINGS).meme_blocked)


class SubtitleTests(unittest.TestCase):
    def test_no_overlapping_events_and_long_words_shrink(self):
        groups = [[("ANOTHER", 0.0, 0.5), ("AIRCRAFT.", 0.5, 1.0)], [("INTERNATIONALIZATION", 1.2, 1.8), ("AIRPORT", 1.8, 2.3)]]
        ass = build_ass(groups)
        events = [l for l in ass.splitlines() if l.startswith("Dialogue")]

        def secs(t):
            h, m, rest = t.split(":")
            return int(h) * 3600 + int(m) * 60 + float(rest)

        spans = sorted((secs(e.split(",")[1]), secs(e.split(",")[2])) for e in events)
        for (_, end), (start, _) in zip(spans, spans[1:]):
            self.assertLessEqual(end, start + 1e-6)
        self.assertIn("\\fs60", ass)


class BrollTests(unittest.TestCase):
    def test_pick_file_prefers_largest_portrait_under_1080(self):
        video = {"video_files": [
            {"file_type": "video/mp4", "width": 2160, "height": 3840, "link": "uhd"},
            {"file_type": "video/mp4", "width": 1080, "height": 1920, "link": "hd"},
            {"file_type": "video/mp4", "width": 720, "height": 1280, "link": "sd"},
            {"file_type": "video/mp4", "width": 1920, "height": 1080, "link": "landscape"}]}
        self.assertEqual(broll.pick_file(video)["link"], "hd")

    def test_fetch_clips_downloads_and_survives_errors(self):
        import os
        os.environ["PEXELS_API_KEY"] = "KEY"
        try:
            payload = {"videos": [{"id": 7, "video_files": [{"file_type": "video/mp4", "width": 1080, "height": 1920, "link": "https://v/7.mp4"}]}]}
            http = FakeHttp({("GET", "api.pexels.com"): [FakeResponse(200, payload)],
                             ("GET", "https://v/7.mp4"): [FakeResponse(200, text="x" * 50)]})
            with tempfile.TemporaryDirectory() as tmp:
                clips = broll.fetch_clips(http, ["airport", "terminal"], Path(tmp), 1)
                self.assertEqual([c.name for c in clips], ["broll_7.mp4"])
            failing = FakeHttp({("GET", "api.pexels.com"): [FakeResponse(500, text="boom")]})
            with tempfile.TemporaryDirectory() as tmp:
                self.assertEqual(broll.fetch_clips(failing, ["airport"], Path(tmp)), [])
        finally:
            os.environ.pop("PEXELS_API_KEY", None)


@unittest.skipUnless(have_ffmpeg(), "ffmpeg not installed")
class MediaTests(unittest.TestCase):
    def test_audio_mix_with_voice_and_with_silent_voice(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = Path(tmp)
            run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=300:d=6", "-af", "volume=0.3",
                 "-ac", "2", "-ar", "44100", str(w / "voice.wav")])
            pad = make_pad(w / "pad.wav", 7, "seed")
            out = mix_audio(w / "voice.wav", 6.0, [0, 2.0, 4.0], pad, False, SETTINGS, w, w / "mix.wav")
            self.assertAlmostEqual(probe_duration(out), 6.0, delta=0.15)
            out2 = mix_audio(w / "voice.wav", 6.0, [0, 2.0], pad, True, SETTINGS, w, w / "mix2.wav")
            self.assertAlmostEqual(probe_duration(out2), 6.0, delta=0.15)

    def test_render_with_broll_clip_music_and_mascot(self):
        from newsroom.media.render import render_reel
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "w"
            work.mkdir()
            clip = work / "broll_1.mp4"
            run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=720x1280:d=3:r=30",
                 "-pix_fmt", "yuv420p", str(clip)])
            beats = [{"kind": "hook", "say": "A short hook for the test.", "screen": "SHORT HOOK", "mood": "shocked"},
                     {"kind": "point", "say": "One fact here.", "screen": "One fact", "n": "1", "mood": "none"},
                     {"kind": "cta", "say": "Follow for more.", "screen": "FOLLOW", "mood": "celebrating"}]
            import newsroom.media.render as render_module
            original = render_module.fetch_clips
            render_module.fetch_clips = lambda *a, **k: [clip]
            try:
                result = render_reel(beats, "Source: Test", ["airport"], work, SETTINGS, Http(offline=True), "sT", "Flight test")
            finally:
                render_module.fetch_clips = original
            self.assertTrue(result.broll)
            info = probe(result.video)
            streams = {s["codec_type"] for s in info["streams"]}
            self.assertEqual(streams, {"video", "audio"})
            self.assertEqual(result.music, "synth-pad")


class FailLoudlyTests(unittest.TestCase):
    def test_run_turns_red_when_llm_fails_on_every_story(self):
        import os
        from newsroom.demo import write_demo_feeds
        from newsroom.pipeline import PipelineError, build_context, stage_collect, stage_process

        class BrokenLLM:
            name = "broken"

            def json(self, *a, **k):
                raise LLMError("gemini HTTP 429: quota")

        with tempfile.TemporaryDirectory() as tmp:
            settings = load_settings(root=ROOT, db_path=f"{tmp}/db.sqlite3", out_dir=f"{tmp}/out", log_dir=f"{tmp}/logs",
                                     offline=True, dry_run=True)
            settings.sources = {"rss": write_demo_feeds(Path(tmp) / "feeds"), "tiers": {}, "syndication_groups": [],
                                "blocklist_domains": []}
            ctx = build_context(settings, llm=BrokenLLM())
            stage_collect(ctx)
            with self.assertRaises(PipelineError):
                stage_process(ctx, 3)
            ctx.store.close()


if __name__ == "__main__":
    unittest.main()
