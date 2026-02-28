from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """全局配置，从 .env 文件或环境变量加载"""

    # API Keys
    google_api_key: str
    tavily_api_key: str
    jina_api_key: str = ""  # 可选

    # LLM 配置
    llm_model_name: str = "gemini-2.5-pro"
    llm_temperature: float = 0.2

    # Agent 配置
    max_searches: int = 5

    # 成本控制（M5 使用，提前预留）
    max_tavily_calls: int = 15    # 单次运行 Tavily 安全上限（高于 max_searches，作为兜底）
    max_budget_usd: float = 0.50  # 单次运行费用上限（美元）

    # 数据库（M4 使用，提前预留）
    database_url: str = "sqlite:///data/job_analysis.db"

    # 通知渠道（M5 使用，提前预留）
    notification_channel: str = "console"
    feishu_webhook_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例"""
    return Settings()
