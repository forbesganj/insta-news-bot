import unittest
from unittest import mock

from newsroom.llm.providers import GROQ_PREFERRED, LLMError, OpenAICompatProvider
from tests.helpers import FakeResponse


class FakeRequests:
    def __init__(self, get_payload, post_responses):
        self.get_payload, self.post_responses, self.posts, self.gets = get_payload, list(post_responses), [], 0

    def get(self, url, **kw):
        self.gets += 1
        return FakeResponse(200, self.get_payload)

    def post(self, url, **kw):
        self.posts.append(kw["json"])
        return self.post_responses.pop(0)


OK = FakeResponse(200, {"choices": [{"message": {"content": '{"a": 1}'}}]})


class ProviderTests(unittest.TestCase):
    def make(self, ids, posts):
        fake = FakeRequests({"data": [{"id": i} for i in ids]}, posts)
        p = OpenAICompatProvider("K", "https://api.groq.com/openai/v1", None, preferred=GROQ_PREFERRED)
        return p, fake

    def test_discovers_best_available_model_when_old_one_is_gone(self):
        p, fake = self.make(["whisper-large-v3", "openai/gpt-oss-20b", "openai/gpt-oss-120b"], [OK])
        with mock.patch("newsroom.llm.providers.requests", fake):
            self.assertEqual(p.json("t", "s", "p"), {"a": 1})
        self.assertEqual(fake.posts[0]["model"], "openai/gpt-oss-120b")

    def test_falls_back_to_any_chat_model_and_skips_audio_models(self):
        p, fake = self.make(["whisper-large-v3", "playai-tts", "some/new-chat-model"], [OK])
        with mock.patch("newsroom.llm.providers.requests", fake):
            p.json("t", "s", "p")
        self.assertEqual(fake.posts[0]["model"], "some/new-chat-model")

    def test_retries_without_json_mode_when_rejected(self):
        bad = FakeResponse(400, {"error": {"message": "response_format json_object is not supported"}})
        p, fake = self.make(["openai/gpt-oss-120b"], [bad, OK])
        with mock.patch("newsroom.llm.providers.requests", fake):
            self.assertEqual(p.json("t", "s", "p"), {"a": 1})
        self.assertIn("response_format", fake.posts[0])
        self.assertNotIn("response_format", fake.posts[1])

    def test_waits_on_rate_limit_then_succeeds(self):
        limited = FakeResponse(429, {"error": {"message": "slow down"}}, headers={"retry-after": "1"})
        p, fake = self.make(["openai/gpt-oss-120b"], [limited, OK])
        with mock.patch("newsroom.llm.providers.requests", fake), mock.patch("newsroom.llm.providers.time.sleep"):
            self.assertEqual(p.json("t", "s", "p"), {"a": 1})

    def test_no_chat_models_is_a_clear_error(self):
        p, fake = self.make(["whisper-large-v3"], [OK])
        with mock.patch("newsroom.llm.providers.requests", fake):
            with self.assertRaises(LLMError):
                p.json("t", "s", "p")


if __name__ == "__main__":
    unittest.main()
