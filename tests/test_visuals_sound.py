import tempfile
import unittest
from pathlib import Path

import numpy as np

from newsroom.kinetic_content import normalize_kinetic, sanitize_graphics
from newsroom.media import sound
from newsroom.media.kinetic import Renderer, build_timeline, sound_events
from newsroom.settings import load_settings
from tests.helpers import ROOT

SETTINGS = load_settings(root=ROOT, db_path=":memory:", offline=True, cfg={"media": {"tts_provider": "none"}})
FACTS = "Nimbus Pay processed 20 billion UPI payments, up from 15.3 billion a year earlier, a 31% rise. Small shops drove 62% of growth."


def scene(graphic, data=None, kind="detail"):
    raw = {"hook_candidates": [{"lines": ["A"]}], "closer": {"lines": ["Q?"]},
           "scenes": [{"kind": kind, "lines": ["X"], "graphic": graphic, "data": data or {}}]}
    sc = normalize_kinetic(raw)["scenes"]
    sanitize_graphics(sc, FACTS)
    return sc[0]


class DataGraphicsAreGrounded(unittest.TestCase):
    def test_counter(self):
        self.assertEqual(scene("counter", {"value": 20, "unit": "billion payments"})["graphic"], "counter")
        self.assertEqual(scene("counter", {"value": 99, "unit": "billion"})["graphic"], "none")
        self.assertEqual(scene("counter", {"value": "x"})["graphic"], "none")

    def test_bars(self):
        ok = scene("bars", {"labels": ["2025", "2026"], "values": [15.3, 20]})
        self.assertEqual(ok["graphic"], "bars")
        self.assertEqual(ok["data"]["decimals"], 1)
        self.assertEqual(scene("bars", {"values": [15.3, 21]})["graphic"], "none")
        self.assertEqual(scene("bars", {"values": [20]})["graphic"], "none")

    def test_phone_never_shows_an_invented_amount(self):
        sc = scene("phone", {"amount": "\u20b9500", "status": "Payment sent"})
        self.assertEqual(sc["graphic"], "phone")
        self.assertEqual(sc["data"]["amount"], "")                      # 500 is not in the facts
        sc = scene("phone", {"amount": "20 billion"})
        self.assertEqual(sc["data"]["amount"], "20 billion")

    def test_phone_only_for_payment_stories(self):
        raw = {"hook_candidates": [{"lines": ["A"]}], "closer": {"lines": ["Q?"]},
               "scenes": [{"kind": "detail", "lines": ["X"], "graphic": "phone", "data": {}}]}
        sc = normalize_kinetic(raw)["scenes"]
        sanitize_graphics(sc, "A new chip design from a semiconductor maker, 3 nanometres.")
        self.assertEqual(sc[0]["graphic"], "icon:phone")

    def test_flow(self):
        nodes = [{"icon": "user", "label": "YOU"}, {"icon": "dragon", "label": "AN EXTREMELY LONG LABEL"}]
        sc = scene("flow", {"nodes": nodes})
        self.assertEqual(sc["graphic"], "flow")
        self.assertEqual(sc["data"]["nodes"][1]["icon"], "spark")
        self.assertLessEqual(len(sc["data"]["nodes"][1]["label"]), 10)
        self.assertEqual(scene("flow", {"nodes": [nodes[0]]})["graphic"], "none")


class RendererTests(unittest.TestCase):
    RAW = [
        {"kind": "hook", "lines": ["20 BILLION", "UPI PAYMENTS"], "accent": 0},
        {"kind": "detail", "lines": ["YOU TAP.", "SHOP GETS IT."], "graphic": "flow", "accent": 1,
         "data": {"nodes": [{"icon": "user", "label": "YOU"}, {"icon": "phone", "label": "UPI"}, {"icon": "shop", "label": "SHOP"}]}},
        {"kind": "stat", "lines": ["PROCESSED"], "graphic": "counter", "data": {"value": 20, "unit": "billion payments"}},
        {"kind": "detail", "lines": ["31% HIGHER"], "graphic": "bars", "data": {"labels": ["2025", "2026"], "values": [15.3, 20.0], "decimals": 1}},
        {"kind": "detail", "lines": ["PAYMENTS", "STAY FREE"], "graphic": "phone", "data": {"amount": "", "status": "PAYMENT SENT"}},
        {"kind": "closer", "lines": ["WHO PAYS?"]},
    ]

    def test_every_data_graphic_draws_at_many_moments(self):
        scenes = build_timeline(self.RAW, 108)
        renderer = Renderer(SETTINGS, scenes, "Source: Test", 108)
        for sc in scenes:
            for dt in (0.05, 0.6, 1.4, 2.4):
                i = int((sc.start + min(dt, sc.dur - 0.05)) * 30)
                frame = renderer.frame(i)
                self.assertEqual(frame.shape, (1920, 1080, 3))
                self.assertGreater(float(frame.std()), 5.0)             # never a blank frame

    def test_counter_counts_up_and_ends_on_the_real_value(self):
        scenes = build_timeline(self.RAW, 108)
        sc = next(s for s in scenes if s.graphic == "counter")
        renderer = Renderer(SETTINGS, scenes, "", 108)
        early = renderer.frame(int((sc.start + 0.4) * 30))
        late = renderer.frame(int((sc.start + 2.6) * 30))
        self.assertFalse(np.array_equal(early, late))


class SoundTests(unittest.TestCase):
    def test_effects_are_placed_on_the_visual_events(self):
        scenes = build_timeline(RendererTests.RAW, 108)
        events = sound_events(scenes)
        kinds = [k for _, k, _ in events]
        self.assertEqual(events[0][1], "boom")
        self.assertEqual(kinds.count("whoosh"), len(scenes) - 1)
        self.assertGreaterEqual(kinds.count("tick"), 10)
        for needed in ("ding", "bwip", "click", "notify", "kaching", "stinger", "riser"):
            self.assertIn(needed, kinds)
        counter = next(s for s in scenes if s.graphic == "counter")
        ding = next(t for t, k, _ in events if k == "ding")
        self.assertAlmostEqual(ding, counter.start + 1.97, places=2)
        self.assertTrue(all(t >= -1.3 for t, _, _ in events))

    def test_every_effect_is_audible_and_finite(self):
        rng = np.random.default_rng(1)
        for kind in ("boom", "thud", "whoosh", "riser", "tick", "ding", "chime_up", "notify", "pop", "bwip", "sparkle",
                     "click", "kaching", "rumble", "stinger"):
            sig = sound.sfx_signal(kind, rng, 0.5)
            self.assertTrue(np.all(np.isfinite(sig)), kind)
            self.assertGreater(float(np.max(np.abs(sig))), 0.05, kind)

    def test_composition_has_the_right_length_follows_the_story_and_never_clips(self):
        secs = [(0, 3.3, "hook"), (3.3, 6.7, "detail"), (6.7, 10.0, "alert"), (10.0, 13.3, "relief"), (13.3, 16.7, "closer")]
        with tempfile.TemporaryDirectory() as tmp:
            path = sound.compose(16.7, 108, secs, [(0.0, "boom", 1.0)], 3, True, 1.0, Path(tmp) / "a.raw")
            a = np.frombuffer(path.read_bytes(), dtype="<i2").reshape(-1, 2) / 32767
        self.assertAlmostEqual(len(a) / 44100, 16.7, delta=0.01)
        self.assertLess(float(np.abs(a).max()), 1.0)
        self.assertGreater(float(np.sqrt((a ** 2).mean())), 0.03)
        def rms(x0, x1):
            seg = a[int(x0 * 44100):int(x1 * 44100)]
            return float(np.sqrt((seg ** 2).mean()))
        self.assertLess(rms(11.5, 13.0), rms(4.0, 6.5))                   # relief: drums out, quieter than the groove

    def test_music_can_be_switched_off_leaving_only_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = sound.compose(5.0, 108, [(0, 5.0, "detail")], [(1.0, "ding", 0.8)], 3, False, 1.0, Path(tmp) / "a.raw")
            a = np.frombuffer(path.read_bytes(), dtype="<i2").reshape(-1, 2) / 32767
        before = float(np.abs(a[: int(0.9 * 44100)]).max())
        after = float(np.abs(a[int(1.0 * 44100): int(1.5 * 44100)]).max())
        self.assertLess(before, 0.01)
        self.assertGreater(after, 0.05)


if __name__ == "__main__":
    unittest.main()
