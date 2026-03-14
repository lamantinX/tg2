import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, PropertyMock, patch

from app.ai import DEFAULT_MAIN_SYSTEM_PROMPT
from app.config import Settings, _sqlite_url_to_absolute, settings
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

    async def test_list_accounts_for_menu_does_not_fetch_names(self) -> None:
        account = SimpleNamespace(id=1, account_name=None, auth_status="authorized", is_active=True)
        service = AccountService.__new__(AccountService)
        service.repo = SimpleNamespace(list=AsyncMock(return_value=[account]))
        service.ensure_account_name = AsyncMock()

        result = await AccountService.list_accounts_for_menu(service)

        self.assertEqual(result, [account])
        service.ensure_account_name.assert_not_called()

    async def test_audit_auto_pauses_bindings_instead_of_deleting(self) -> None:
        account = SimpleNamespace(id=1, phone="+100", session_name="100", proxy_url=None)
        service = AccountService.__new__(AccountService)
        service.session = object()
        service.repo = SimpleNamespace(
            list=AsyncMock(return_value=[account]),
            mark_status=AsyncMock(return_value=account),
        )
        service.binding_repo = SimpleNamespace(
            auto_pause_for_account=AsyncMock(return_value=2),
            resume_auto_paused_for_account=AsyncMock(return_value=0),
        )
        tg = SimpleNamespace(
            check_health=AsyncMock(return_value={"auth_status": "revoked", "is_active": False, "reason": "session revoked"}),
            disconnect=AsyncMock(),
        )

        with patch("app.services.proxy_manager.get_proxy_for_account", AsyncMock(return_value=None)), patch(
            "app.services.TelegramAccountClient", return_value=tg
        ):
            report = await AccountService.audit_accounts(service)

        self.assertEqual(report["paused_bindings"], 2)
        self.assertEqual(report["resumed_bindings"], 0)
        service.binding_repo.auto_pause_for_account.assert_awaited_once_with(1, "session revoked")
        service.binding_repo.resume_auto_paused_for_account.assert_not_called()

    async def test_audit_resumes_auto_paused_bindings_when_account_recovers(self) -> None:
        account = SimpleNamespace(id=1, phone="+100", session_name="100", proxy_url=None)
        service = AccountService.__new__(AccountService)
        service.session = object()
        service.repo = SimpleNamespace(
            list=AsyncMock(return_value=[account]),
            mark_status=AsyncMock(return_value=account),
        )
        service.binding_repo = SimpleNamespace(
            auto_pause_for_account=AsyncMock(return_value=0),
            resume_auto_paused_for_account=AsyncMock(return_value=3),
        )
        tg = SimpleNamespace(
            check_health=AsyncMock(return_value={"auth_status": "authorized", "is_active": True, "reason": "ok"}),
            disconnect=AsyncMock(),
        )

        with patch("app.services.proxy_manager.get_proxy_for_account", AsyncMock(return_value=None)), patch(
            "app.services.TelegramAccountClient", return_value=tg
        ):
            report = await AccountService.audit_accounts(service)

        self.assertEqual(report["paused_bindings"], 0)
        self.assertEqual(report["resumed_bindings"], 3)
        service.binding_repo.resume_auto_paused_for_account.assert_awaited_once_with(1)
        service.binding_repo.auto_pause_for_account.assert_not_called()

    async def test_audit_keeps_previously_active_account_on_transient_lock_error(self) -> None:
        account = SimpleNamespace(
            id=1,
            phone="+100",
            session_name="100",
            proxy_url=None,
            auth_status="authorized",
            is_active=True,
        )
        service = AccountService.__new__(AccountService)
        service.session = object()
        service.repo = SimpleNamespace(
            list=AsyncMock(return_value=[account]),
            mark_status=AsyncMock(return_value=account),
        )
        service.binding_repo = SimpleNamespace(
            auto_pause_for_account=AsyncMock(return_value=0),
            resume_auto_paused_for_account=AsyncMock(return_value=0),
        )
        tg = SimpleNamespace(
            check_health=AsyncMock(return_value={"auth_status": "error", "is_active": False, "reason": "database is locked"}),
            disconnect=AsyncMock(),
        )

        with patch("app.services.proxy_manager.get_proxy_for_account", AsyncMock(return_value=None)), patch(
            "app.services.TelegramAccountClient", return_value=tg
        ):
            report = await AccountService.audit_accounts(service)

        self.assertEqual(report["active"], 1)
        self.assertEqual(report["inactive"], 0)
        self.assertEqual(report["details"][0]["auth_status"], "authorized")
        self.assertTrue(report["details"][0]["is_active"])
        self.assertIn("database is locked", report["details"][0]["reason"])
        service.repo.mark_status.assert_awaited_once_with(
            account,
            auth_status="authorized",
            is_active=True,
            touch_last_login=False,
        )
        self.assertEqual(service.repo.mark_status.await_args.kwargs["touch_last_login"], False)
        service.binding_repo.auto_pause_for_account.assert_not_called()
        service.binding_repo.resume_auto_paused_for_account.assert_awaited_once_with(1)

    async def test_audit_restores_false_inactive_account_when_last_login_exists(self) -> None:
        account = SimpleNamespace(
            id=1,
            phone="+100",
            session_name="100",
            proxy_url=None,
            auth_status="error",
            is_active=False,
            last_login_at=datetime.now(timezone.utc),
        )
        service = AccountService.__new__(AccountService)
        service.session = object()
        service.repo = SimpleNamespace(
            list=AsyncMock(return_value=[account]),
            mark_status=AsyncMock(return_value=account),
        )
        service.binding_repo = SimpleNamespace(
            auto_pause_for_account=AsyncMock(return_value=0),
            resume_auto_paused_for_account=AsyncMock(return_value=0),
        )
        tg = SimpleNamespace(
            check_health=AsyncMock(
                return_value={
                    "auth_status": "error",
                    "is_active": False,
                    "reason": "Connection to Telegram failed 3 time(s)",
                }
            ),
            disconnect=AsyncMock(),
        )

        with patch("app.services.proxy_manager.get_proxy_for_account", AsyncMock(return_value=None)), patch(
            "app.services.TelegramAccountClient", return_value=tg
        ):
            report = await AccountService.audit_accounts(service)

        self.assertEqual(report["active"], 1)
        self.assertEqual(report["inactive"], 0)
        self.assertEqual(report["details"][0]["auth_status"], "authorized")
        self.assertTrue(report["details"][0]["is_active"])
        self.assertIn("Connection to Telegram failed 3 time(s)", report["details"][0]["reason"])
        service.repo.mark_status.assert_awaited_once_with(
            account,
            auth_status="authorized",
            is_active=True,
            touch_last_login=False,
        )
        service.binding_repo.auto_pause_for_account.assert_not_called()
        service.binding_repo.resume_auto_paused_for_account.assert_awaited_once_with(1)

    async def test_check_account_resumes_bindings_and_syncs_name_for_healthy_account(self) -> None:
        account = SimpleNamespace(
            id=1,
            phone="+100",
            session_name="100",
            proxy_url=None,
            account_name=None,
        )
        updated_account = SimpleNamespace(
            id=1,
            phone="+100",
            session_name="100",
            proxy_url=None,
            account_name="Alice Smith",
            auth_status="authorized",
            is_active=True,
        )
        service = AccountService.__new__(AccountService)
        service.session = object()
        service.repo = SimpleNamespace(
            get=AsyncMock(return_value=account),
            update_profile=AsyncMock(return_value=updated_account),
            mark_status=AsyncMock(return_value=updated_account),
        )
        service.binding_repo = SimpleNamespace(
            auto_pause_for_account=AsyncMock(return_value=0),
            resume_auto_paused_for_account=AsyncMock(return_value=2),
        )
        tg = SimpleNamespace(
            check_health=AsyncMock(return_value={"auth_status": "authorized", "is_active": True, "reason": "ok"}),
            get_account_name=AsyncMock(return_value="Alice Smith"),
            disconnect=AsyncMock(),
        )

        with patch("app.services.proxy_manager.get_proxy_for_account", AsyncMock(return_value=None)), patch(
            "app.services.TelegramAccountClient", return_value=tg
        ):
            result = await AccountService.check_account(service, 1)

        self.assertEqual(result["account"].account_name, "Alice Smith")
        self.assertEqual(result["auth_status"], "authorized")
        self.assertTrue(result["is_active"])
        self.assertEqual(result["resumed_bindings"], 2)
        service.repo.update_profile.assert_awaited_once()
        service.binding_repo.resume_auto_paused_for_account.assert_awaited_once_with(1)
        service.binding_repo.auto_pause_for_account.assert_not_called()

    async def test_check_account_pauses_bindings_for_revoked_account(self) -> None:
        account = SimpleNamespace(
            id=1,
            phone="+100",
            session_name="100",
            proxy_url=None,
            account_name="Alice Smith",
        )
        updated_account = SimpleNamespace(
            id=1,
            phone="+100",
            session_name="100",
            proxy_url=None,
            account_name="Alice Smith",
            auth_status="revoked",
            is_active=False,
        )
        service = AccountService.__new__(AccountService)
        service.session = object()
        service.repo = SimpleNamespace(
            get=AsyncMock(return_value=account),
            mark_status=AsyncMock(return_value=updated_account),
        )
        service.binding_repo = SimpleNamespace(
            auto_pause_for_account=AsyncMock(return_value=3),
            resume_auto_paused_for_account=AsyncMock(return_value=0),
        )
        tg = SimpleNamespace(
            check_health=AsyncMock(return_value={"auth_status": "revoked", "is_active": False, "reason": "session revoked"}),
            disconnect=AsyncMock(),
        )

        with patch("app.services.proxy_manager.get_proxy_for_account", AsyncMock(return_value=None)), patch(
            "app.services.TelegramAccountClient", return_value=tg
        ):
            result = await AccountService.check_account(service, 1)

        self.assertEqual(result["auth_status"], "revoked")
        self.assertFalse(result["is_active"])
        self.assertEqual(result["paused_bindings"], 3)
        service.binding_repo.auto_pause_for_account.assert_awaited_once_with(1, "session revoked")
        service.binding_repo.resume_auto_paused_for_account.assert_not_called()

    async def test_delete_account_cascades_bindings_and_session_cleanup(self) -> None:
        account = SimpleNamespace(id=1, session_name="100")
        service = AccountService.__new__(AccountService)
        service.repo = SimpleNamespace(
            get=AsyncMock(return_value=account),
            delete=AsyncMock(return_value=1),
        )
        service.binding_repo = SimpleNamespace(delete_by_account_id=AsyncMock(return_value=4))

        with patch.object(type(settings), "resolved_data_dir", new_callable=PropertyMock, return_value=Path("/tmp/tg2")), patch(
            "pathlib.Path.unlink"
        ) as unlink_mock, patch("pathlib.Path.exists", return_value=True):
            result = await AccountService.delete_account(service, 1)

        self.assertEqual(result["deleted_bindings"], 4)
        self.assertTrue(result["deleted"])
        service.binding_repo.delete_by_account_id.assert_awaited_once_with(1)
        service.repo.delete.assert_awaited_once_with(account)
        unlink_mock.assert_called_once()

    async def test_delete_account_ignores_missing_session_file(self) -> None:
        account = SimpleNamespace(id=1, session_name="100")
        service = AccountService.__new__(AccountService)
        service.repo = SimpleNamespace(
            get=AsyncMock(return_value=account),
            delete=AsyncMock(return_value=1),
        )
        service.binding_repo = SimpleNamespace(delete_by_account_id=AsyncMock(return_value=0))

        with patch.object(type(settings), "resolved_data_dir", new_callable=PropertyMock, return_value=Path("/tmp/tg2")), patch(
            "pathlib.Path.exists", return_value=False
        ), patch("pathlib.Path.unlink") as unlink_mock:
            result = await AccountService.delete_account(service, 1)

        self.assertTrue(result["deleted"])
        unlink_mock.assert_not_called()


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

    def test_resolve_state_marks_auto_paused_before_schedule(self) -> None:
        service = BindingService.__new__(BindingService)
        state = BindingService._resolve_state(service, True, True, True, None, datetime.now())
        self.assertEqual(state, "auto_paused")


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


class SettingsPathTests(unittest.TestCase):
    def test_sqlite_relative_url_becomes_absolute(self) -> None:
        base_dir = Path.cwd()
        result = _sqlite_url_to_absolute("sqlite+aiosqlite:///./data/app.db", base_dir)

        self.assertNotIn("./data", result)
        self.assertTrue(result.startswith("sqlite+aiosqlite:///"))
        self.assertTrue(result.endswith("/data/app.db"))

    def test_resolved_data_dir_is_absolute(self) -> None:
        self.assertTrue(settings.resolved_data_dir.is_absolute())

    def test_relative_ai_log_path_uses_configured_data_dir(self) -> None:
        custom_settings = Settings(data_dir="runtime-data", ai_log_path="logs/ai.log")

        self.assertEqual(
            custom_settings.resolved_ai_log_path,
            (custom_settings.resolved_data_dir / "logs" / "ai.log").resolve(),
        )

    def test_ai_log_path_strips_default_data_prefix_inside_custom_data_dir(self) -> None:
        custom_settings = Settings(data_dir="runtime-data", ai_log_path="data/logs/ai.log")

        self.assertEqual(
            custom_settings.resolved_ai_log_path,
            (custom_settings.resolved_data_dir / "logs" / "ai.log").resolve(),
        )


if __name__ == "__main__":
    unittest.main()
