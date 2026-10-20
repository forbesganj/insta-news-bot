import unittest

from newsroom.gates import (align_hookscores, builtin_hook_score, parse_beats, parse_caption, parse_detect,
                            parse_hookscore)

# Output formats published in the upstream README.
HOOKS_OUT = """HOOK RANKING
==============================================================================
->  85.6 STRONG  Nobody tells you that your first 30 reels are supposed to f...
        weakest: SPECIFICITY (75)
    81.4 STRONG  I lost $18,000 because of one missing contract.
        weakest: ADDRESS (70)
    54.4 OK      Stop scrolling if you want to grow on Instagram in 2026
        weakest: STAKES (70)
        dealbreaker: Opens with "stop scrolling". Asking for attention proves
                     you have not earned it.
     9.6 WEAK    Hey guys, today I wanted to talk about content strategy
        weakest: FRONTLOAD (0)
        dealbreaker: Greeting. Nobody came to the feed to be greeted.
"""
BEATS_OUT = """BEAT SHEET  \u00b7  73 words  \u00b7  ~26.6s at 165 wpm  \u00b7  target 30.0s
========================================================================
  0:00.0   2.9s  HOOK    I lost $18,000 because of one missing contract.
  0:02.9  14.6s  MID     Here is the exact clause I now put in every single...
                         ^ 14.6s on one beat
  0:17.4   2.9s          Section 4. Payment on delivery, not on approval.
  0:23.3   3.3s  CTA     That one word change is worth $18,000 to me.
------------------------------------------------------------------------
  - Beat 2 runs past 4s. Either split the line.
"""
CAPTION_OUT = """PASS  LENGTH            389 / 2200 characters
WARN  FIRST LINE        133 characters, so it gets cut at 125 mid-thought
FAIL  HASHTAGS          9 hashtags, the cap is 5
"""
DETECT_OUT = """BURSTINESS    ###################.....  78.4
SPECIFICITY   #############...........  53.6    3.4 concrete markers per 100 words
HUMAN SCORE   ###################.....  79.7   PASS    (+47.2)
"""


class ParserTests(unittest.TestCase):
    def test_hookscore(self):
        entries = parse_hookscore(HOOKS_OUT)
        self.assertEqual([e["score"] for e in entries], [85.6, 81.4, 54.4, 9.6])
        self.assertIn("you have not earned it", entries[2]["dealbreaker"])
        hooks = ["I lost $18,000 because of one missing contract.",
                 "Hey guys, today I wanted to talk about content strategy",
                 "Nobody tells you that your first 30 reels are supposed to flop."]
        aligned = align_hookscores(entries, hooks)
        self.assertEqual([a["score"] for a in aligned], [81.4, 9.6, 85.6])

    def test_beats(self):
        parsed = parse_beats(BEATS_OUT)
        self.assertEqual(parsed["words"], 73)
        self.assertAlmostEqual(parsed["duration"], 26.6)
        self.assertEqual(parsed["beats"][0]["tag"], "HOOK")
        self.assertEqual(len(parsed["beats"]), 4)
        self.assertTrue(parsed["warnings"])

    def test_caption(self):
        parsed = parse_caption(CAPTION_OUT)
        self.assertEqual((parsed["fails"], parsed["warns"]), (1, 1))

    def test_detect(self):
        parsed = parse_detect(DETECT_OUT)
        self.assertAlmostEqual(parsed["score"], 79.7)
        self.assertEqual(parsed["verdict"], "PASS")
        self.assertAlmostEqual(parsed["checks"]["BURSTINESS"], 78.4)

    def test_unrecognised_output_returns_none(self):
        self.assertIsNone(parse_beats("garbage"))
        self.assertIsNone(parse_caption("garbage"))
        self.assertIsNone(parse_detect("garbage"))

    def test_builtin_hook_score_ordering(self):
        good, _ = builtin_hook_score("India processed 20 billion UPI payments in a month.")
        greeting, _ = builtin_hook_score("Hey guys, today let's talk about payments")
        self.assertGreater(good, 55)
        self.assertLess(greeting, 10)


if __name__ == "__main__":
    unittest.main()
