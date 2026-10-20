"""Proves the wrapper really finds, runs and parses the upstream scripts (using README-format stubs)."""
import tempfile
import textwrap
import unittest
from pathlib import Path

from newsroom.gates import UpstreamTools, gate_beats, gate_caption, gate_hooks, gate_human
from newsroom.settings import load_settings
from tests.helpers import ROOT

HOOKSCORE = textwrap.dedent('''
    import sys
    hooks = [l.strip() for l in open(sys.argv[1]) if l.strip()]
    rows = sorted(((90.0 if any(c.isdigit() for c in h) else 20.0, h) for h in hooks), reverse=True)
    print("HOOK RANKING")
    for i, (score, h) in enumerate(rows):
        label = "STRONG" if score >= 80 else "WEAK"
        print(("->  " if i == 0 else "    ") + f"{score:.1f} {label}  " + (h[:60] + "..." if len(h) > 60 else h))
        print("        weakest: STAKES (50)")
        if score < 30:
            print("        dealbreaker: no number, no stakes")
''')
BEATS = textwrap.dedent('''
    import sys
    lines = [l.strip() for l in open(sys.argv[1]) if l.strip()]
    words = sum(len(l.split()) for l in lines)
    print(f"BEAT SHEET  \\u00b7  {words} words  \\u00b7  ~{words/165*60:.1f}s at 165 wpm  \\u00b7  target 30.0s")
    t = 0.0
    for i, l in enumerate(lines):
        d = len(l.split())/165*60
        tag = "HOOK" if i == 0 else ("CTA" if i == len(lines)-1 else "MID")
        print(f"  0:{t:04.1f}  {d:.1f}s  {tag}  {l}")
        t += d
''')
CAPTION = textwrap.dedent('''
    import sys
    text = open(sys.argv[1]).read()
    print("PASS  LENGTH            %d / 2200 characters" % len(text))
    print("PASS  HASHTAGS          ok")
''')
DETECT = textwrap.dedent('''
    print("HUMAN SCORE   ###########...........  42.0   FLAGGED")
''')


class UpstreamIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        for skill, name, body in (("ig-reel", "hookscore.py", HOOKSCORE), ("ig-reel", "beats.py", BEATS),
                                  ("ig-caption", "caption.py", CAPTION), ("ig-human", "detect.py", DETECT)):
            folder = root / "skills" / skill
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_text(body)
        self.settings = load_settings(root=ROOT, db_path=":memory:", offline=True)
        self.tools = UpstreamTools(root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_tools_are_found(self):
        found = self.tools.available()
        self.assertTrue(found["hookscore"] and found["beats"] and found["caption"] and found["detect"])
        self.assertFalse(found["humanize"])

    def test_hook_gate_uses_upstream_and_picks_best(self):
        hooks = ["Hey guys welcome back", "India processed 20 billion UPI payments"]
        index, rows, gate = gate_hooks(hooks, self.tools, self.settings)
        self.assertEqual(gate.engine, "upstream")
        self.assertEqual(index, 1)
        self.assertTrue(rows[0]["dealbreaker"])

    def test_beats_caption_and_human_gates(self):
        lines = ["India processed 20 billion UPI payments in one month."] + ["Short filler sentence number %d goes here today." % i for i in range(8)]
        beats = gate_beats(lines, self.tools, self.settings)
        self.assertEqual(beats.engine, "upstream")
        caption = gate_caption("UPI crossed 20 billion.\n\nSource: Mint\n\n#upi", ["upi"], self.tools, self.settings)
        self.assertEqual(caption.engine, "upstream")
        self.assertTrue(caption.passed)
        human = gate_human("Some text here.", self.tools, self.settings)
        self.assertEqual(human.engine, "upstream")
        self.assertFalse(human.passed)          # 42.0 < threshold 60

    def test_pipeline_own_files_are_never_mistaken_for_upstream(self):
        root = Path(self.tmp.name)
        (root / "newsroom").mkdir()
        (root / "newsroom" / "humanize.py").write_text("print('mine')")
        self.assertFalse(UpstreamTools(root).available()["humanize"])

    def test_missing_scripts_fall_back_to_builtin(self):
        empty = UpstreamTools(Path(tempfile.mkdtemp()))
        _, _, gate = gate_hooks(["India processed 20 billion UPI payments in one month."], empty, self.settings)
        self.assertEqual(gate.engine, "builtin")

    def test_strict_mode_fails_closed_when_unparseable(self):
        settings = load_settings(root=ROOT, db_path=":memory:", offline=True, cfg={"gates": {"upstream_strict": True}})
        broken = Path(self.tmp.name) / "skills" / "ig-human" / "detect.py"
        broken.write_text('print("totally different output")')
        tools = UpstreamTools(Path(self.tmp.name))
        self.assertFalse(gate_human("Some text.", tools, settings).passed)


if __name__ == "__main__":
    unittest.main()
