import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.services import AccountService, BindingService


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


if __name__ == "__main__":
    unittest.main()
