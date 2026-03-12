import unittest
from unittest.mock import patch

from app.ai import AIService, DEFAULT_MAIN_SYSTEM_PROMPT
from app.config import settings


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class FakeAsyncClient:
    last_json = None

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *args, **kwargs):
        FakeAsyncClient.last_json = kwargs.get("json")
        return FakeResponse({"output_text": "[AI] Test reply"})


class AIServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_stub_keeps_disclosure(self) -> None:
        with patch.object(settings, "openai_api_key", ""):
            result = await AIService().generate_reply("chat42", ["hello"])
        self.assertTrue(result.startswith(settings.ai_disclosure_prefix))

    async def test_openai_path_uses_response_output(self) -> None:
        FakeAsyncClient.last_json = None
        with patch.object(settings, "openai_api_key", "test-key"):
            with patch("app.ai.httpx.AsyncClient", FakeAsyncClient):
                result = await AIService().generate_reply("chat42", ["hello"])
        self.assertEqual(result, "[AI] Test reply")

    async def test_openai_path_accepts_binding_system_prompt(self) -> None:
        FakeAsyncClient.last_json = None
        with patch.object(settings, "openai_api_key", "test-key"):
            with patch("app.ai.httpx.AsyncClient", FakeAsyncClient):
                await AIService().generate_reply(
                    "chat42",
                    ["hello"],
                    system_prompt="write shorter",
                    recent_self_messages=["old reply"],
                )
        self.assertIsNotNone(FakeAsyncClient.last_json)
        system_text = FakeAsyncClient.last_json["input"][0]["content"][0]["text"]
        user_text = FakeAsyncClient.last_json["input"][1]["content"][0]["text"]
        self.assertIn("Chat-specific instructions", system_text)
        self.assertIn("write shorter", system_text)
        self.assertIn("your_recent_messages", user_text)
        self.assertIn("old reply", user_text)

    async def test_openai_path_accepts_main_system_prompt_override(self) -> None:
        FakeAsyncClient.last_json = None
        with patch.object(settings, "openai_api_key", "test-key"):
            with patch("app.ai.httpx.AsyncClient", FakeAsyncClient):
                await AIService().generate_reply(
                    "chat42",
                    ["hello"],
                    main_system_prompt="Custom global prompt",
                )
        self.assertIsNotNone(FakeAsyncClient.last_json)
        system_text = FakeAsyncClient.last_json["input"][0]["content"][0]["text"]
        self.assertIn("Custom global prompt", system_text)
        self.assertNotIn(DEFAULT_MAIN_SYSTEM_PROMPT, system_text)

    def test_skip_generated_reply_when_it_repeats_loop_keywords(self) -> None:
        service = AIService()
        context = [
            {"sender": "a", "text": "pesik rulit idyom k riviera"},
            {"sender": "b", "text": "pesik rulit i kofe zovyot"},
            {"sender": "c", "text": "pesik rulit kto eshche idet"},
            {"sender": "d", "text": "pesik rulit riviera ryadom"},
            {"sender": "e", "text": "pesik rulit more ryadom"},
            {"sender": "f", "text": "pesik rulit za kofe idem"},
        ]
        should_skip, reason = service.should_skip_generated_reply(
            "da pesik rulit riviera kofe",
            context_messages=context,
            recent_self_messages=["pesik rulit i more ryadom"],
        )
        self.assertTrue(should_skip)
        self.assertIn(reason, {"too_similar_to_own_recent", "repeats_loop_keywords", "too_similar_to_context"})

    def test_keep_generated_reply_when_it_switches_topic(self) -> None:
        service = AIService()
        context = [
            {"sender": "a", "text": "pesik rulit idyom k riviera"},
            {"sender": "b", "text": "pesik rulit i kofe zovyot"},
            {"sender": "c", "text": "pesik rulit kto eshche idet"},
            {"sender": "d", "text": "pesik rulit riviera ryadom"},
        ]
        should_skip, reason = service.should_skip_generated_reply(
            "a kto novyi film uzhe smotrel",
            context_messages=context,
            recent_self_messages=["da pesik rulit"],
        )
        self.assertFalse(should_skip)
        self.assertEqual(reason, "")


if __name__ == "__main__":
    unittest.main()
