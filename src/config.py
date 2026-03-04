from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Global configuration, loaded from .env or environment variables"""

    # API Keys
    google_api_key: str
    tavily_api_key: str
    jina_api_key: str = ""       # Optional: Jina Reader API
    serper_api_key: str = ""     # Optional: Google Search via Serper API

    # LLM config
    llm_model_name: str = "gemini-2.5-pro"
    llm_temperature: float = 0.2

    # Agent config
    max_searches: int = 20       # Total Tavily search budget per run

    # Cost control (M5)
    max_tavily_calls: int = 30   # Hard Tavily safety cap (above max_searches as fallback)
    max_budget_usd: float = 1.00 # Per-run cost limit (USD)

    # Database (M4)
    database_url: str = "sqlite:///data/job_analysis.db"

    # Notifications (M5)
    notification_channel: str = "console"
    feishu_webhook_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    """Get global settings singleton"""
    return Settings()
