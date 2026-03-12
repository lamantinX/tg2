import unittest

from app.config import settings
from app.proxy_manager import DecodoproxyManager


class DecodoProxyManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original = {
            "decodo_proxy_username": settings.decodo_proxy_username,
            "decodo_proxy_password": settings.decodo_proxy_password,
            "decodo_proxy_scheme": settings.decodo_proxy_scheme,
            "decodo_proxy_host": settings.decodo_proxy_host,
            "decodo_proxy_port": settings.decodo_proxy_port,
            "decodo_proxy_country": settings.decodo_proxy_country,
            "decodo_proxy_session_duration": settings.decodo_proxy_session_duration,
        }

    def tearDown(self) -> None:
        for key, value in self.original.items():
            setattr(settings, key, value)

    def test_builds_sticky_proxy_url(self) -> None:
        settings.decodo_proxy_username = "user-customer42"
        settings.decodo_proxy_password = "secret"
        settings.decodo_proxy_scheme = "socks5h"
        settings.decodo_proxy_host = "gate.decodo.com"
        settings.decodo_proxy_port = 7000
        settings.decodo_proxy_country = ""
        settings.decodo_proxy_session_duration = 30

        manager = DecodoproxyManager()
        proxy_url = manager._build_proxy_url("abc123")

        self.assertEqual(
            proxy_url,
            "socks5h://user-customer42-sessionduration-30-session-abc123:secret@gate.decodo.com:7000",
        )

    def test_builds_country_specific_proxy_url(self) -> None:
        settings.decodo_proxy_username = "user-customer42"
        settings.decodo_proxy_password = "secret"
        settings.decodo_proxy_scheme = "http"
        settings.decodo_proxy_host = "gate.decodo.com"
        settings.decodo_proxy_port = 7000
        settings.decodo_proxy_country = "us"
        settings.decodo_proxy_session_duration = 10

        manager = DecodoproxyManager()
        proxy_url = manager._build_proxy_url("sticky")

        self.assertEqual(
            proxy_url,
            "http://user-customer42-country-us-sessionduration-10-session-sticky:secret@gate.decodo.com:7000",
        )


if __name__ == "__main__":
    unittest.main()
