"""Centralised configuration loaded from environment / .env file."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    google_api_key: str = ""
    tavily_api_key: str = ""
    jina_api_key: str = ""
    serper_api_key: str = ""
    scraper_api_key: str = ""

    koshien_max_scraper_calls: int = 8


settings = Settings()
