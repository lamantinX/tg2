import asyncio
import unittest
from types import SimpleNamespace

from app.bot import (
    _account_button_text,
    _binding_button_text,
    binding_settings_keyboard,
    build_bot,
    format_account_details,
    format_audit_report,
    format_binding_settings,
    format_help,
    main_menu_keyboard,
)


class BindingPresentationTests(unittest.TestCase):
    def test_binding_button_text_uses_requested_columns(self) -> None:
        binding = SimpleNamespace(
            id=12,
            account_id=3,
            chat_ref="@chat_ref",
            chat_title="Project Chat",
            account_name="Alice",
        )

        text = _binding_button_text(binding)

        self.assertEqual(text, "12 | 3 | Alice | Project Chat")

    def test_binding_settings_prefers_chat_title_and_account_name(self) -> None:
        binding = SimpleNamespace(
            id=8,
            account_id=5,
            chat_ref="-100123",
            chat_title="Team Room",
            account_name="Helper",
            interval_min_minutes=10,
            interval_max_minutes=15,
            reply_interval_min_minutes=None,
            reply_interval_max_minutes=None,
            context_message_count=12,
            auto_paused=True,
            auto_pause_reason="session revoked",
            auto_paused_at=None,
            system_prompt=None,
            last_posted_at=None,
            next_run_at=None,
            last_reply_posted_at=None,
            next_reply_run_at=None,
        )

        text = format_binding_settings(binding)

        self.assertIn("account_name=Helper", text)
        self.assertIn("chat_title=Team Room", text)
        self.assertIn("chat_ref=-100123", text)
        self.assertIn("auto_paused=1", text)
        self.assertIn("auto_pause_reason=session revoked", text)

    def test_binding_settings_keyboard_includes_force_send_action(self) -> None:
        keyboard = binding_settings_keyboard(8)
        labels = [button.text for row in keyboard.inline_keyboard for button in row]

        self.assertIn("Сгенерировать и отправить сообщение", labels)

    def test_audit_report_uses_pause_and_resume_counters(self) -> None:
        text = format_audit_report(
            {
                "audited": 2,
                "active": 1,
                "inactive": 1,
                "paused_bindings": 3,
                "resumed_bindings": 1,
                "details": [
                    {
                        "account_id": 7,
                        "phone": "+100",
                        "auth_status": "revoked",
                        "is_active": False,
                        "paused_bindings": 3,
                        "resumed_bindings": 0,
                        "reason": "session revoked",
                    }
                ],
            }
        )

        self.assertIn("paused_bindings=3", text)
        self.assertIn("resumed_bindings=1", text)
        self.assertIn("paused=3", text)
        self.assertIn("resumed=0", text)


class AccountPresentationTests(unittest.TestCase):
    def test_account_button_text_uses_name_and_phone(self) -> None:
        account = SimpleNamespace(account_name="Alice Smith", phone="+15550000001")

        text = _account_button_text(account)

        self.assertEqual(text, "Alice Smith | +15550000001")

    def test_account_details_show_name_phone_and_character(self) -> None:
        account = SimpleNamespace(
            id=7,
            account_name="Alice Smith",
            phone="+15550000001",
            auth_status="authorized",
            is_active=True,
            character=SimpleNamespace(name="Helper"),
        )

        text = format_account_details(account)

        self.assertIn("Alice Smith", text)
        self.assertIn("+15550000001", text)
        self.assertIn("authorized", text)
        self.assertIn("Helper", text)

    def test_account_details_handle_missing_character(self) -> None:
        account = SimpleNamespace(
            id=9,
            account_name="Alice Smith",
            phone="+15550000001",
            auth_status="revoked",
            is_active=False,
            character=None,
        )

        text = format_account_details(account)

        self.assertIn("Alice Smith", text)
        self.assertIn("+15550000001", text)
        self.assertIn("Не назначен", text)

    def test_help_text_is_human_readable(self) -> None:
        text = format_help()

        self.assertIn("Справка", text)
        self.assertIn("Основной промпт", text)

    def test_main_menu_uses_readable_labels(self) -> None:
        keyboard = main_menu_keyboard()
        labels = [button.text for row in keyboard.inline_keyboard for button in row]

        self.assertIn("Мастер", labels)
        self.assertIn("Инструкция", labels)
        self.assertIn("Аккаунты", labels)
        self.assertIn("Проверить прокси", labels)

    def test_start_handler_sends_human_readable_menu_text(self) -> None:
        _, dispatcher = build_bot()
        start_handler = next(
            handler.callback
            for handler in dispatcher.message.handlers
            if handler.callback.__name__ == "start_handler"
        )

        class FakeMessage:
            def __init__(self) -> None:
                self.calls: list[tuple[str, object]] = []

            async def answer(self, text: str, reply_markup=None):
                self.calls.append((text, reply_markup))

        class FakeState:
            async def clear(self) -> None:
                return None

        message = FakeMessage()
        asyncio.run(start_handler(message, FakeState()))

        self.assertEqual(len(message.calls), 1)
        text, _ = message.calls[0]
        self.assertEqual(
            text,
            "Главное меню. Используй кнопки ниже или команду /wizard.",
        )


if __name__ == "__main__":
    unittest.main()
