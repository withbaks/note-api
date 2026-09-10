from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_env_file() -> str | None:
    """Locate .env by walking up from this file (works from any cwd)."""
    for directory in Path(__file__).resolve().parents:
        env_path = directory / ".env"
        if env_path.is_file():
            return str(env_path)
    return None


_env_file = _find_env_file()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_file,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://note:note@localhost:5432/note"
    database_url_sync: str = "postgresql://note:note@localhost:5432/note"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = "change-me"
    jwt_access_expire_minutes: int = 15
    jwt_refresh_expire_days: int = 30
    openai_api_key: str = ""
    ai_model: str = "gpt-4o-mini"
    apple_client_id: str | None = None
    apple_skip_verify: bool = False
    environment: str = "development"
    cors_origins: str = "*"
    web_base_url: str = "http://localhost:8081"
    whatsapp_secret: str = ""
    whatsapp_default_user_id: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
