import unittest
from unittest.mock import patch

from app.ai import AIService
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
        return FakeResponse({"output_text": "[AI] Тестовый ответ из OpenAI"})


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
        self.assertEqual(result, "[AI] Тестовый ответ из OpenAI")

    async def test_openai_path_accepts_binding_system_prompt(self) -> None:
        FakeAsyncClient.last_json = None
        with patch.object(settings, "openai_api_key", "test-key"):
            with patch("app.ai.httpx.AsyncClient", FakeAsyncClient):
                await AIService().generate_reply("chat42", ["hello"], system_prompt="пиши короче")
        self.assertIsNotNone(FakeAsyncClient.last_json)
        system_text = FakeAsyncClient.last_json["input"][0]["content"][0]["text"]
        self.assertIn("Дополнительные инструкции для этого чата", system_text)
        self.assertIn("пиши короче", system_text)


if __name__ == "__main__":
    unittest.main()
