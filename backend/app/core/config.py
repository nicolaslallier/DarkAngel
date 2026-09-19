from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, overridable via environment variables or a .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="DARKANGEL_", extra="ignore")

    app_name: str = "DarkAngel API"
    version: str = "0.1.0"
    debug: bool = False
    # Origins allowed to call the API (the Vite dev server by default).
    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
