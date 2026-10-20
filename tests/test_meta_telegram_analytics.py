import unittest
from datetime import timedelta

from newsroom.analytics import collector, optimizer
from newsroom.publish.meta import InstagramClient, MetaError, refresh_long_lived_token
from newsroom.publish.telegram import TelegramClient
from newsroom.settings import load_settings
from newsroom.store import Store, iso, utcnow
from tests.helpers import ROOT, FakeHttp, FakeResponse


def client(http):
    return InstagramClient("TOKEN", "1789", http, "v25.0", sleep=lambda s: None)


class MetaTests(unittest.TestCase):
    def test_publish_reel_happy_path(self):
        http = FakeHttp({
            ("POST", "/1789/media_publish"): [FakeResponse(200, {"id": "MEDIA1"})],
            ("POST", "/1789/media"): [FakeResponse(200, {"id": "CONT1"})],
            ("GET", "/CONT1"): [FakeResponse(200, {"status_code": "IN_PROGRESS"}), FakeResponse(200, {"status_code": "FINISHED"})],
            ("GET", "/MEDIA1"): [FakeResponse(200, {"permalink": "https://instagram.com/reel/abc/"})],
        })
        seen = []
        out = client(http).publish_reel("https://cdn/x.mp4", "caption", 0, 0, 300, on_container=seen.append)
        self.assertEqual(out["media_id"], "MEDIA1")
        self.assertEqual(out["permalink"], "https://instagram.com/reel/abc/")
        self.assertEqual(seen, ["CONT1"])
        create = http.calls[0]
        self.assertEqual(create[2]["json"], {"media_type": "REELS", "video_url": "https://cdn/x.mp4", "caption": "caption"})
        self.assertEqual(create[2]["headers"]["Authorization"], "Bearer TOKEN")
        self.assertIn("graph.instagram.com/v25.0/1789/media", create[1])

    def test_container_error_raises(self):
        http = FakeHttp({("POST", "/1789/media"): [FakeResponse(200, {"id": "C"})],
                         ("GET", "/C"): [FakeResponse(200, {"status_code": "ERROR"})]})
        with self.assertRaises(MetaError):
            client(http).publish_reel("https://cdn/x.mp4", "c", 0, 0, 300)

    def test_error_classification(self):
        http = FakeHttp({("POST", "/media"): [FakeResponse(400, {"error": {"message": "Invalid OAuth access token", "code": 190}})]})
        with self.assertRaises(MetaError) as ctx:
            client(http).create_reel_container("u", "c")
        self.assertEqual(ctx.exception.kind, "token")
        http = FakeHttp({("POST", "/media"): [FakeResponse(400, {"error": {"message": "limit", "code": 4}})]})
        with self.assertRaises(MetaError) as ctx:
            client(http).create_reel_container("u", "c")
        self.assertEqual(ctx.exception.kind, "rate_limit")

    def test_insights_fallback_per_metric(self):
        http = FakeHttp({("GET", "/M1/insights"): [
            FakeResponse(400, {"error": {"message": "bad metric", "code": 100}}),
            FakeResponse(200, {"data": [{"name": "reach", "values": [{"value": 120}]}]}),
            FakeResponse(400, {"error": {"message": "unsupported", "code": 100}}),
            FakeResponse(200, {"data": [{"name": "saved", "total_value": {"value": 7}}]})]})
        values, unsupported = client(http).media_insights("M1", ["reach", "views", "saved"])
        self.assertEqual(values, {"reach": 120.0, "saved": 7.0})
        self.assertEqual(unsupported, ["views"])

    def test_refresh_token(self):
        http = FakeHttp({("GET", "refresh_access_token"): [FakeResponse(200, {"access_token": "NEW", "expires_in": 5184000})]})
        self.assertEqual(refresh_long_lived_token(http, "OLD")["access_token"], "NEW")
        self.assertEqual(http.calls[0][2]["params"]["grant_type"], "ig_refresh_token")


class TelegramTests(unittest.TestCase):
    def test_poll_decisions_filters_chat_and_advances_offset(self):
        updates = {"ok": True, "result": [
            {"update_id": 10, "callback_query": {"id": "q1", "data": "a:s123", "message": {"chat": {"id": 42}, "message_id": 5}}},
            {"update_id": 11, "callback_query": {"id": "q2", "data": "r:s999", "message": {"chat": {"id": 7}, "message_id": 6}}},
            {"update_id": 12, "callback_query": {"id": "q3", "data": "r:s456", "message": {"chat": {"id": 42}, "message_id": 7}}}]}
        http = FakeHttp({("POST", "getUpdates"): [FakeResponse(200, updates)],
                         ("POST", "answerCallbackQuery"): [FakeResponse(200, {"ok": True})],
                         ("POST", "editMessageReplyMarkup"): [FakeResponse(200, {"ok": True})]})
        store = Store(":memory:")
        tg = TelegramClient("T", "42", http)
        self.assertEqual(tg.poll_decisions(store), [("s123", "approve"), ("s456", "reject")])
        self.assertEqual(store.kv_get("tg_offset"), "13")


class AnalyticsTests(unittest.TestCase):
    def test_collector_uses_latest_due_checkpoint_once(self):
        s = load_settings(root=ROOT, db_path=":memory:", offline=True)
        store = Store(":memory:")
        pid = store.add_post("s1", "M1", "p", False, "ai", 23, "a.com", 30.0)
        store.conn.execute("UPDATE posts SET published_at=?", (iso(utcnow() - timedelta(hours=80)),))
        store.conn.commit()
        http = FakeHttp({("GET", "/M1/insights"): [FakeResponse(200, {"data": [{"name": "reach", "values": [{"value": 500}]}]})]})
        stats = collector.collect(store, s, client(http))
        self.assertEqual(stats["fetched"], 1)
        self.assertTrue(store.has_metrics(pid, 72))
        self.assertEqual(collector.collect(store, s, client(http))["fetched"], 0)

    def test_optimizer_boosts_better_pillar_and_respects_bounds(self):
        s = load_settings(root=ROOT, db_path=":memory:", offline=True)
        store = Store(":memory:")
        data = [("ai", 2000, 80), ("ai", 2500, 90), ("ai", 3000, 70), ("money", 300, 5), ("money", 250, 4), ("money", 400, 6)]
        for i, (pillar, reach, engagement) in enumerate(data):
            pid = store.add_post(f"s{i}", f"M{i}", None, False, pillar, 23 if pillar == "ai" else 24, "a.com", 30.0)
            store.save_metrics(pid, 72, {"reach": reach, "likes": engagement, "shares": engagement / 10, "saved": engagement / 10, "comments": 1}, None, {})
        weights = optimizer.update_weights(store, s)
        self.assertGreater(weights["pillar"]["ai"], 1.0)
        self.assertLess(weights["pillar"]["money"], 1.0)
        for value in weights["pillar"].values():
            self.assertTrue(0.7 <= value <= 1.4)
        self.assertAlmostEqual(store.get_weights("pillar")["ai"], weights["pillar"]["ai"], places=2)

    def test_optimizer_waits_for_enough_data(self):
        s = load_settings(root=ROOT, db_path=":memory:", offline=True)
        self.assertEqual(optimizer.update_weights(Store(":memory:"), s), {})


if __name__ == "__main__":
    unittest.main()
