import random
import unittest
from datetime import timedelta

from newsroom.dedupe import cluster_items, find_duplicate, ingest
from newsroom.models import Fact, RawItem
from newsroom.score import score_story, select_stories
from newsroom.settings import load_settings
from newsroom.store import Store, iso, utcnow
from newsroom.verify import verify_story
from tests.helpers import ROOT


def settings():
    return load_settings(root=ROOT, db_path=":memory:", offline=True)


def src(domain, tier=2, hours=2, title="x", url=None):
    return {"domain": domain, "tier": tier, "published_at": iso(utcnow() - timedelta(hours=hours)), "title": title,
            "url": url or f"https://{domain}/{title}", "source": domain, "origin": "rss", "fetchable": True,
            "summary": "", "extra": {}}


FACTS = [Fact(f"fact number {i} with 20 billion", "e", "u") for i in range(3)]


class VerifyTests(unittest.TestCase):
    def test_two_independent_sources_pass(self):
        story = {"title": "Big launch", "sources": [src("a.com"), src("b.com")]}
        self.assertTrue(verify_story(story, FACTS, settings()).passed)

    def test_single_source_fails(self):
        story = {"title": "Big launch", "sources": [src("a.com")]}
        result = verify_story(story, FACTS, settings())
        self.assertFalse(result.passed)
        self.assertTrue(result.reasons[0].startswith("verify:"))

    def test_syndicated_outlets_count_once(self):
        story = {"title": "Big launch", "sources": [src("indiatimes.com"), src("timesofindia.com")]}
        self.assertFalse(verify_story(story, FACTS, settings()).passed)

    def test_single_tier1_passes(self):
        story = {"title": "RBI announces rule", "sources": [src("rbi.org.in", tier=1)]}
        self.assertTrue(verify_story(story, FACTS, settings()).passed)

    def test_stale_fails(self):
        story = {"title": "Old news", "sources": [src("a.com", hours=80), src("b.com", hours=79)]}
        self.assertFalse(verify_story(story, FACTS, settings()).passed)

    def test_too_few_facts_fails(self):
        story = {"title": "Big launch", "sources": [src("a.com"), src("b.com")]}
        self.assertFalse(verify_story(story, FACTS[:2], settings()).passed)

    def test_rumour_needs_strong_confirmation(self):
        story = {"title": "Rumoured chip leaks", "sources": [src("a.com"), src("b.com")]}
        self.assertFalse(verify_story(story, FACTS, settings()).passed)

    def test_sensitive_topic_forces_review(self):
        story = {"title": "Court rules on fraud case", "sources": [src("a.com"), src("b.com")]}
        result = verify_story(story, FACTS, settings())
        self.assertTrue(result.passed)
        self.assertTrue(result.needs_review)
        self.assertEqual(result.sensitivity, "high")

    def test_stock_tips_rejected(self):
        story = {"title": "Infosys target price raised, should you buy?", "sources": [src("a.com"), src("b.com")]}
        self.assertFalse(verify_story(story, FACTS, settings()).passed)


class DedupeTests(unittest.TestCase):
    def item(self, title, domain="a.com", url=None):
        return RawItem(title=title, url=url or f"https://{domain}/{abs(hash(title))}", source=domain, domain=domain,
                       tier=2, published_at=iso(utcnow()), summary="s")

    def test_clustering_groups_same_event(self):
        items = [self.item("Nimbus Pay crosses 20 billion UPI transactions in a single month", "a.com"),
                 self.item("Nimbus Pay crosses 20 billion UPI transactions in August", "b.com"),
                 self.item("Orbit Labs releases faster language model", "c.com")]
        clusters = cluster_items(items, settings().get("dedupe"))
        self.assertEqual(sorted(len(c.items) for c in clusters), [1, 2])

    def test_ingest_merges_and_blocks_duplicates(self):
        s = settings()
        store = Store(":memory:")
        first = ingest(store, [self.item("Nimbus Pay crosses 20 billion UPI transactions", "a.com")], s)
        self.assertEqual(first["new_stories"], 1)
        again = ingest(store, [self.item("Nimbus Pay crosses 20 billion UPI transactions", "a.com",
                                         url="https://a.com/same")], s)
        self.assertEqual(again["merged"], 1)
        self.assertEqual(len(store.list_stories()), 1)
        story = store.list_stories()[0]
        store.update_story(story["id"], status="published")
        third = ingest(store, [self.item("Nimbus Pay crosses 20 billion UPI transactions this month", "c.com")], s)
        self.assertEqual(third["new_stories"], 0)
        self.assertEqual(third["duplicates"], 1)

    def test_same_url_is_not_reprocessed(self):
        s = settings()
        store = Store(":memory:")
        item = self.item("Orbit Labs releases faster language model", "a.com", url="https://a.com/orbit")
        ingest(store, [item], s)
        stats = ingest(store, [item], s)
        self.assertEqual(stats["items_seen"], 1)

    def test_find_duplicate_against_posted(self):
        store = Store(":memory:")
        sid = store.create_story("Nimbus Pay crosses 20 billion UPI transactions", {"nimbus", "pay", "cross", "20", "billion", "upi", "transaction"}, [], "money")
        store.update_story(sid, status="published")
        from newsroom.textutil import tokens
        dup = find_duplicate(store, tokens("Nimbus Pay crosses 20 billion UPI transactions"), settings().get("dedupe"))
        self.assertIsNotNone(dup)


class ScoreTests(unittest.TestCase):
    def test_fresh_multi_source_beats_old_single(self):
        s = settings()
        fresh = {"title": "OpenAI launches new AI model with 70 billion parameters", "pillar": "ai",
                 "sources": [src("a.com", hours=1), src("b.com", hours=2), src("openai.com", tier=1, hours=1)]}
        old = {"title": "Something happened", "pillar": "ai", "sources": [src("a.com", hours=30)]}
        self.assertGreater(score_story(fresh, s)[0], score_story(old, s)[0])

    def test_learned_multiplier_changes_score(self):
        s = settings()
        story = {"title": "AI model launch", "pillar": "ai", "sources": [src("a.com"), src("b.com")]}
        base = score_story(story, s)[0]
        boosted = score_story(story, s, {"ai": 1.4})[0]
        self.assertAlmostEqual(boosted / base, 1.4, places=1)

    def test_select_is_greedy_without_exploration(self):
        stories = [{"id": str(i), "score": float(i)} for i in range(10)]
        chosen = select_stories(stories, 3, 0.0, random.Random(1))
        self.assertEqual([c["id"] for c in chosen], ["9", "8", "7"])


if __name__ == "__main__":
    unittest.main()
