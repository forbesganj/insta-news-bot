import tempfile
import unittest
from pathlib import Path

from newsroom.content import ContentRejected
from newsroom.gates import UpstreamTools
from newsroom.kinetic_content import KineticContentFactory, normalize_kinetic, sanitize_graphics
from newsroom.media.kinetic import PALETTES, Renderer, build_timeline, render_kinetic
from newsroom.media.util import have_ffmpeg, probe
from newsroom.settings import load_settings
from newsroom.store import Store
from tests.helpers import ROOT

SETTINGS = load_settings(root=ROOT, db_path=":memory:", offline=True,
                         cfg={"kinetic": {"fps": 10}, "qa": {"min_fps": 8}, "media": {"tts_provider": "none"}})

RAW = [
    {"kind": "hook", "lines": ["A POWER BANK", "TURNED", "A FLIGHT BACK"], "accent": 1},
    {"kind": "detail", "lines": ["INDIGO 6E 6115", "SUNDAY"], "graphic": "route", "label": "Hyderabad"},
    {"kind": "alert", "lines": ["SMOKE."], "sub": "from a device", "graphic": "smoke"},
    {"kind": "detail", "lines": ["THE CREW", "TOOK CONTROL."], "accent": 1},
    {"kind": "relief", "lines": ["LANDED SAFELY."], "graphic": "check"},
    {"kind": "twist", "lines": ["THEN THEY FLEW", "ON TO", "CHENNAI"], "sub": "on another plane", "accent": 2},
    {"kind": "closer", "lines": ["DO YOU CARRY", "A POWER BANK", "ON FLIGHTS?"], "sub": "Tell me below.", "accent": 2},
]


class TimelineTests(unittest.TestCase):
    def test_cuts_land_on_the_beat_and_colours_never_repeat(self):
        scenes = build_timeline(RAW, 108)
        beat = 60 / 108
        for sc in scenes:
            self.assertAlmostEqual(sc.dur / beat, round(sc.dur / beat), places=6)
        for a, b in zip(scenes, scenes[1:]):
            self.assertNotEqual(a.palette, b.palette)
            self.assertAlmostEqual(a.end, b.start, places=6)
        self.assertEqual(scenes[0].palette, "ink")
        self.assertEqual(scenes[-1].kind, "closer")
        self.assertLessEqual(scenes[-1].end, 30.0)

    def test_overlong_stories_drop_middle_scenes(self):
        raw = [RAW[0]] + [{"kind": "detail", "lines": ["ONE TWO THREE", "FOUR FIVE SIX", "SEVEN EIGHT"]}] * 9 + [RAW[-1]]
        scenes = build_timeline(raw, 108, 30.0)
        self.assertLessEqual(scenes[-1].end, 30.0 + 1e-6)
        self.assertEqual(scenes[0].kind, "hook")
        self.assertEqual(scenes[-1].kind, "closer")

    def test_every_palette_exists(self):
        for sc in build_timeline(RAW, 108):
            self.assertIn(sc.palette, PALETTES)


class LayoutTests(unittest.TestCase):
    def test_text_stays_inside_the_screen_and_clear_of_the_bottom_ui(self):
        scenes = build_timeline(RAW + [{"kind": "closer", "lines": ["EXTRAORDINARILYLONGWORDTHATMUSTSHRINK", "B", "C", "D"]}], 108)
        renderer = Renderer(SETTINGS, scenes, "Source: Test", 108)
        for sc in scenes:
            zone = "top" if (sc.graphic == "route" or sc.graphic.startswith("icon:")) else ("bottom" if sc.graphic == "check" else "")
            lines, sub_y = renderer.layout(sc, zone)
            for text, size, cy in lines:
                self.assertGreaterEqual(size, 44)
                self.assertLessEqual(renderer.sprite(text, size, (255, 255, 255)).width * 1.0, 1080 + 80)
                self.assertLess(cy + size * 0.6, 1480, f"{text} runs into the caption/UI zone")
                self.assertGreater(cy - size * 0.6, 280)

    def test_frames_have_the_right_shape(self):
        scenes = build_timeline(RAW, 108)
        renderer = Renderer(SETTINGS, scenes, "Source: Test", 108)
        for i in (0, 20, 100, 300, int(scenes[-1].end * 30) - 1):
            self.assertEqual(renderer.frame(i).shape, (1920, 1080, 3))


class ContentTests(unittest.TestCase):
    FACTS = [{"text": "IndiGo flight 6E 6115 was diverted to Hyderabad on Sunday after smoke from a device", "evidence": "e"},
             {"text": "The flight landed safely and the crew controlled the situation", "evidence": "e"},
             {"text": "Passengers continued to Chennai on another aircraft", "evidence": "e"}]

    def story(self):
        return {"id": "s1", "title": "IndiGo flight diverted to Hyderabad after smoke from a device", "pillar": "tech",
                "facts": self.FACTS, "sources": [{"source": "NDTV", "domain": "ndtv.com", "tier": 2}],
                "verification": {"meme_blocked": True}}

    def factory(self, raw):
        class Fake:
            name = "fake"
            calls = 0

            def json(self, *a, **k):
                Fake.calls += 1
                return raw if Fake.calls == 1 or not isinstance(raw, list) else raw[1]

        llm = Fake()
        return KineticContentFactory(SETTINGS, llm, UpstreamTools(Path(tempfile.mkdtemp())), Store(":memory:"))

    GOOD = {
        "hook_candidates": [{"formula_id": 23, "lines": ["A POWER BANK", "TURNED", "A FLIGHT BACK"], "accent": 1}],
        "scenes": [
            {"kind": "detail", "lines": ["INDIGO 6E 6115", "SUNDAY"], "graphic": "route", "label": "Hyderabad"},
            {"kind": "alert", "lines": ["SMOKE."], "sub": "from a device", "graphic": "smoke"},
            {"kind": "detail", "lines": ["THE CREW", "TOOK CONTROL."], "accent": 1},
            {"kind": "relief", "lines": ["LANDED SAFELY."], "graphic": "check"},
            {"kind": "twist", "lines": ["THEN ON TO", "CHENNAI"], "sub": "on another plane", "accent": 1}],
        "closer": {"lines": ["DO YOU CARRY", "A POWER BANK", "ON FLIGHTS?"], "sub": "Tell me below."}}

    def test_good_script_builds_scenes_and_caption(self):
        package = self.factory(self.GOOD).build(self.story(), False)
        self.assertEqual(package["style"], "kinetic")
        kinds = [s["kind"] for s in package["scenes"]]
        self.assertEqual(kinds[0], "hook")
        self.assertEqual(kinds[-1], "closer")
        self.assertIn("Source:", package["caption"])
        route = next(s for s in package["scenes"] if s["graphic"] == "route")
        self.assertEqual(route["label"], "Hyderabad")

    def test_invented_number_is_rejected(self):
        bad = {**self.GOOD, "scenes": self.GOOD["scenes"][:4] + [{"kind": "twist", "lines": ["187 PASSENGERS", "ONBOARD"]}]}
        with self.assertRaises(ContentRejected) as ctx:
            self.factory(bad).build(self.story(), False)
        self.assertIn("187", str(ctx.exception))

    def test_first_person_and_exclamations_are_rejected_but_closer_cta_is_allowed(self):
        bad = {**self.GOOD, "scenes": self.GOOD["scenes"][:4] + [{"kind": "twist", "lines": ["WE LOVE", "THIS!"]}]}
        with self.assertRaises(ContentRejected):
            self.factory(bad).build(self.story(), False)

    def test_graphics_are_sanitised(self):
        scenes = normalize_kinetic({"hook_candidates": [{"lines": ["A"]}], "closer": {"lines": ["Q?"]}, "scenes": [
            {"kind": "detail", "lines": ["X"], "graphic": "smoke"},
            {"kind": "detail", "lines": ["X"], "graphic": "route", "label": "Atlantis"},
            {"kind": "detail", "lines": ["X"], "graphic": "check"},
            {"kind": "relief", "lines": ["X"], "graphic": "none"},
            {"kind": "alert", "lines": ["X"], "graphic": "none"}]})["scenes"]
        sanitize_graphics(scenes, "a calm story about taxes")
        self.assertEqual([s["graphic"] for s in scenes], ["none", "icon:plane", "none", "check", "pulse"])


@unittest.skipUnless(have_ffmpeg(), "ffmpeg not installed")
class RenderTests(unittest.TestCase):
    def test_render_produces_a_valid_reel(self):
        raw = [RAW[0], RAW[2], RAW[4], RAW[-1]]
        with tempfile.TemporaryDirectory() as tmp:
            result = render_kinetic(raw, "Source: Test", Path(tmp), SETTINGS, "sTest")
            info = probe(result.video)
            streams = {s["codec_type"]: s for s in info["streams"]}
            self.assertEqual((streams["video"]["width"], streams["video"]["height"]), (1080, 1920))
            self.assertEqual(streams["video"]["codec_name"], "h264")
            self.assertEqual(streams["audio"]["codec_name"], "aac")
            self.assertAlmostEqual(float(info["format"]["duration"]), result.duration, places=2)
            self.assertGreater(result.duration, 8)
            self.assertEqual(result.music, "synth-beat")


if __name__ == "__main__":
    unittest.main()
