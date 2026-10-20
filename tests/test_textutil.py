import unittest

from newsroom import textutil as t
from newsroom.humanize import builtin_human_score, humanize_text


class TextUtilTests(unittest.TestCase):
    def test_numbers_grounding_rules(self):
        found = t.extract_numbers("UPI hit 20.5 billion, \u20b92,000 crore, 5G, 3 times, 2025, 10th, 99%")
        self.assertEqual(found, {"20.5", "2000", "2025", "99"})

    def test_domain_and_canonical(self):
        self.assertEqual(t.domain_of("https://www.theverge.com/a"), "theverge.com")
        self.assertEqual(t.domain_of("https://x.bbc.co.uk/news"), "bbc.co.uk")
        self.assertEqual(t.canonical_url("https://www.Example.com/a/?utm_source=x&id=3#f"), "https://example.com/a?id=3")

    def test_similarity_and_stemming(self):
        a = t.tokens("OpenAI launches new models - TechCrunch")
        b = t.tokens("OpenAI launch new model")
        self.assertGreater(t.jaccard(a, b), 0.9)

    def test_quote_support(self):
        text = "Nimbus Pay processed 20 billion UPI transactions in August 2026, the company said."
        self.assertTrue(t.quote_supported("processed 20 billion UPI transactions in August 2026", text))
        self.assertFalse(t.quote_supported("processed 90 billion crypto trades in March", text))

    def test_html_to_text_prefers_article(self):
        html = ("<html><nav><p>menu menu menu menu menu menu menu menu menu menu menu</p></nav><article>"
                + "<p>" + "Real paragraph about the story. " * 20 + "</p></article><footer><p>"
                + "footer text " * 10 + "</p></footer></html>")
        text = t.html_to_text(html)
        self.assertIn("Real paragraph", text)
        self.assertNotIn("footer text", text)

    def test_humanizer(self):
        result = humanize_text("It\u2019s big \u2014 really.\u200b In today\u2019s video, let\u2019s delve into UPI. Follow for more!")
        self.assertNotIn("\u2014", result.text)
        self.assertNotIn("\u200b", result.text)
        self.assertNotIn("delve", result.text.lower())
        self.assertGreater(builtin_human_score(result.text)[0], 80)
        self.assertLess(builtin_human_score("Stop scrolling!!! It's not just a tool, it's a game-changer.")[0], 70)


if __name__ == "__main__":
    unittest.main()
