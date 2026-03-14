from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _resolve_runtime_path(path_value: str, data_dir: Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path.resolve()

    if path.parts[:1] == ('data',):
        path = Path(*path.parts[1:])

    return (data_dir / path).resolve()


def _sqlite_url_to_absolute(url: str, base_dir: Path) -> str:
    prefixes = ("sqlite+aiosqlite:///", "sqlite:///")
    prefix = next((item for item in prefixes if url.startswith(item)), None)
    if prefix is None:
        return url

    raw_path = url[len(prefix):]
    if raw_path == ":memory:":
        return url

    path = Path(raw_path)
    if not path.is_absolute():
        path = (base_dir / path).resolve()

    posix_path = path.as_posix()
    if path.drive:
        return f"{prefix}{posix_path}"
    return f"{prefix.rstrip('/')}/{posix_path}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    bot_token: str = ""
    telegram_api_id: int = 0
    telegram_api_hash: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_timeout_seconds: int = 60
    database_url: str = "sqlite+aiosqlite:///./data/app.db"
    data_dir: str = "data"
    ai_disclosure_prefix: str = ""
    default_post_interval_minutes: int = 10
    ai_log_path: str = "data/logs/ai.log"
    decodo_api_key: str = ""
    decodo_api_url: str = "https://api.decodo.com/v1"
    decodo_proxy_username: str = ""
    decodo_proxy_password: str = ""
    decodo_proxy_scheme: str = "socks5h"
    decodo_proxy_host: str = "gate.decodo.com"
    decodo_proxy_port: int = 7000
    decodo_proxy_country: str = ""
    decodo_proxy_session_duration: int = 30
    accounts_per_proxy: int = 3

    @property
    def decodo_enabled(self) -> bool:
        return bool(self.decodo_proxy_username and self.decodo_proxy_password)

    @property
    def base_dir(self) -> Path:
        return Path(__file__).resolve().parent.parent

    @property
    def resolved_data_dir(self) -> Path:
        data_dir = Path(self.data_dir)
        if not data_dir.is_absolute():
            data_dir = self.base_dir / data_dir
        return data_dir.resolve()

    @property
    def resolved_database_url(self) -> str:
        return _sqlite_url_to_absolute(self.database_url, self.base_dir)

    @property
    def resolved_ai_log_path(self) -> Path:
        return _resolve_runtime_path(self.ai_log_path, self.resolved_data_dir)


settings = Settings()
