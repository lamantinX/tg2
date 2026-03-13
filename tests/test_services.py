import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.ai import DEFAULT_MAIN_SYSTEM_PROMPT
from app.config import settings
from app.services import AccountService, AppSettingsService, BindingService


class AccountServiceLogicTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_login_requires_requested_code(self) -> None:
        service = AccountService.__new__(AccountService)
        service.repo = SimpleNamespace(
            get=AsyncMock(
                return_value=SimpleNamespace(
                    id=1,
                    phone="+100",
                    phone_code_hash=None,
                    auth_status="new",
                    session_name="100",
                    proxy_url=None,
                )
            )
        )
        with self.assertRaises(ValueError):
            await AccountService.complete_login(service, 1, "12345")


class BindingServiceLogicTests(unittest.TestCase):
    def test_validate_settings_rejects_min_greater_than_max(self) -> None:
        service = BindingService.__new__(BindingService)
        with self.assertRaises(ValueError):
            BindingService._validate_settings(service, 15, 5, 12)

    def test_validate_settings_rejects_invalid_reply_range(self) -> None:
        service = BindingService.__new__(BindingService)
        with self.assertRaises(ValueError):
            BindingService._validate_settings(service, 5, 10, 12, 30, 15)

    def test_resolve_reply_interval_can_disable(self) -> None:
        service = BindingService.__new__(BindingService)
        binding = SimpleNamespace(reply_interval_min_minutes=10, reply_interval_max_minutes=20)
        self.assertEqual(
            BindingService._resolve_reply_interval(service, binding, None, None, True),
            (None, None),
        )


class InMemoryAppSettingsRepo:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get_value(self, key: str) -> str | None:
        return self.values.get(key)

    async def set_value(self, key: str, value: str | None):
        self.values[key] = value or ""
        return SimpleNamespace(key=key, value=value)

    async def delete(self, key: str) -> int:
        return int(self.values.pop(key, None) is not None)


class AppSettingsServiceLogicTests(unittest.IsolatedAsyncioTestCase):
    async def test_main_system_prompt_roundtrip(self) -> None:
        service = AppSettingsService.__new__(AppSettingsService)
        service.repo = InMemoryAppSettingsRepo()

        saved = await AppSettingsService.set_main_system_prompt(service, "  stay brief  ")

        self.assertEqual(saved, "stay brief")
        self.assertEqual(await AppSettingsService.get_main_system_prompt(service), "stay brief")
        self.assertEqual(await AppSettingsService.get_effective_main_system_prompt(service), "stay brief")

        await AppSettingsService.reset_main_system_prompt(service)

        self.assertIsNone(await AppSettingsService.get_main_system_prompt(service))
        self.assertEqual(await AppSettingsService.get_effective_main_system_prompt(service), DEFAULT_MAIN_SYSTEM_PROMPT)

    async def test_main_system_prompt_rejects_empty_value(self) -> None:
        service = AppSettingsService.__new__(AppSettingsService)
        service.repo = InMemoryAppSettingsRepo()

        with self.assertRaises(ValueError):
            await AppSettingsService.set_main_system_prompt(service, "   ")

    async def test_openai_model_roundtrip(self) -> None:
        service = AppSettingsService.__new__(AppSettingsService)
        service.repo = InMemoryAppSettingsRepo()

        saved = await AppSettingsService.set_openai_model(service, "  gpt-5-mini  ")

        self.assertEqual(saved, "gpt-5-mini")
        self.assertEqual(await AppSettingsService.get_openai_model(service), "gpt-5-mini")
        self.assertEqual(await AppSettingsService.get_effective_openai_model(service), "gpt-5-mini")

        await AppSettingsService.reset_openai_model(service)

        self.assertIsNone(await AppSettingsService.get_openai_model(service))
        self.assertEqual(await AppSettingsService.get_effective_openai_model(service), settings.openai_model)

    async def test_openai_model_rejects_empty_value(self) -> None:
        service = AppSettingsService.__new__(AppSettingsService)
        service.repo = InMemoryAppSettingsRepo()

        with self.assertRaises(ValueError):
            await AppSettingsService.set_openai_model(service, "   ")


if __name__ == "__main__":
    unittest.main()
