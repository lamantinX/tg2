import unittest

import socks

from app.proxy import parse_proxy_url


class ProxyParserTests(unittest.TestCase):
    def test_parses_socks5_proxy(self) -> None:
        result = parse_proxy_url("socks5://user:pass@127.0.0.1:1080")
        self.assertEqual(result, (socks.SOCKS5, "127.0.0.1", 1080, True, "user", "pass"))

    def test_parses_http_proxy(self) -> None:
        result = parse_proxy_url("http://10.0.0.2:8080")
        self.assertEqual(result, (socks.HTTP, "10.0.0.2", 8080, False, None, None))

    def test_rejects_unsupported_proxy(self) -> None:
        with self.assertRaises(ValueError):
            parse_proxy_url("ftp://127.0.0.1:21")


if __name__ == "__main__":
    unittest.main()
