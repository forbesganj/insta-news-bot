import os
import tempfile
import unittest
from pathlib import Path

from newsroom.media.subtitles import build_ass, build_srt, chunk_words
from newsroom.media.util import have_ffmpeg
from tests.helpers import ROOT


class SubtitleTests(unittest.TestCase):
    WORDS = [[("India", 0.0, 0.3), ("processed", 0.3, 0.8), ("20", 0.8, 1.0), ("billion", 1.0, 1.4), ("payments", 1.4, 2.0)],
             [("Follow", 2.2, 2.5), ("us", 2.5, 2.7)]]

    def test_chunks_never_cross_beats_and_respect_limits(self):
        ass = build_ass(self.WORDS)
        self.assertIn("Dialogue:", ass)
        self.assertIn("PlayResY: 1920", ass)
        for chunk in chunk_words(self.WORDS[0]):
            self.assertLessEqual(len(chunk), 3)
        srt = build_srt(self.WORDS)
        self.assertTrue(srt.startswith("1\n00:00:00,000 -->"))

    def test_ass_escapes_braces(self):
        ass = build_ass([[("{bad}", 0, 1)]])
        self.assertNotIn("{bad}", ass)


@unittest.skipUnless(have_ffmpeg(), "ffmpeg not installed")
class OfflineEndToEnd(unittest.TestCase):
    def test_demo_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"NEWSROOM_OUT": f"{tmp}/out", "NEWSROOM_DB": f"{tmp}/db.sqlite3", "NEWSROOM_LOGS": f"{tmp}/logs",
                   "LLM_PROVIDER": "offline", "TTS_PROVIDER": "none", "NEWSROOM_OFFLINE": "1",
                   "NEWSROOM_CFG": '{"kinetic": {"fps": 10}, "qa": {"min_fps": 8}}'}
            old = {k: os.environ.get(k) for k in env}
            os.environ.update(env)
            try:
                from newsroom import cli
                self.assertEqual(cli.main(["demo"]), 0)
                from newsroom.store import Store
                store = Store(f"{tmp}/db.sqlite3")
                statuses = {s["status"] for s in store.list_stories()}
                self.assertIn("dry_published", statuses)
                self.assertTrue(any(p["dry_run"] == 1 for p in store.list_posts(False)))
                self.assertEqual(store.list_posts(True), [])          # nothing real was published
                rejected = [s for s in store.list_stories("rejected") if "independent" in str(s["last_error"])]
                self.assertTrue(rejected, "single-source rumour must be rejected")
                videos = list(Path(f"{tmp}/out").glob("s*.mp4"))
                self.assertTrue(videos)
                store.close()
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
