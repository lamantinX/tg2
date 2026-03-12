import unittest
from types import SimpleNamespace

from app.bot import _binding_button_text, format_binding_settings


class BindingPresentationTests(unittest.TestCase):
    def test_binding_button_text_uses_requested_columns(self) -> None:
        binding = SimpleNamespace(
            id=12,
            account_id=3,
            chat_ref='@chat_ref',
            chat_title='Project Chat',
            account_name='Alice',
        )

        text = _binding_button_text(binding)

        self.assertEqual(text, '12 | 3 | Alice | Project Chat')

    def test_binding_settings_prefers_chat_title_and_account_name(self) -> None:
        binding = SimpleNamespace(
            id=8,
            account_id=5,
            chat_ref='-100123',
            chat_title='Team Room',
            account_name='Helper',
            interval_min_minutes=10,
            interval_max_minutes=15,
            reply_interval_min_minutes=None,
            reply_interval_max_minutes=None,
            context_message_count=12,
            system_prompt=None,
            last_posted_at=None,
            next_run_at=None,
            last_reply_posted_at=None,
            next_reply_run_at=None,
        )

        text = format_binding_settings(binding)

        self.assertIn('account_name=Helper', text)
        self.assertIn('chat_title=Team Room', text)
        self.assertIn('chat_ref=-100123', text)


if __name__ == '__main__':
    unittest.main()
