from urllib.parse import unquote, urlparse

import socks

SUPPORTED_PROXY_SCHEMES = {
    "socks5": socks.SOCKS5,
    "socks5h": socks.SOCKS5,
    "socks4": socks.SOCKS4,
    "http": socks.HTTP,
    "https": socks.HTTP,
}


def parse_proxy_url(proxy_url: str | None) -> tuple | None:
    if not proxy_url:
        return None

    parsed = urlparse(proxy_url)
    scheme = parsed.scheme.lower()
    if scheme not in SUPPORTED_PROXY_SCHEMES:
        raise ValueError(f"Unsupported proxy scheme: {parsed.scheme}")
    if not parsed.hostname or not parsed.port:
        raise ValueError("Proxy URL must include host and port")

    username = unquote(parsed.username) if parsed.username else None
    password = unquote(parsed.password) if parsed.password else None
    rdns = scheme in {"socks5", "socks5h", "socks4"}
    return (
        SUPPORTED_PROXY_SCHEMES[scheme],
        parsed.hostname,
        parsed.port,
        rdns,
        username,
        password,
    )
