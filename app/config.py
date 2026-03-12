from pydantic_settings import BaseSettings, SettingsConfigDict


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


settings = Settings()
