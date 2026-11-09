from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite+aiosqlite:///./askpanel.db"
    jwt_secret: str = "development-only-change-me"
    admin_password: str = "change-me"
    cors_origins: str = "http://localhost:3000"
    llm_provider: str = "mock"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    openai_base_url: str = ""
    nebius_api_key: str = ""
    nebius_base_url: str = "https://api.studio.nebius.ai/v1/"
    llm_model: str = "claude-3-5-sonnet-latest"
    llm_temperature: float = 0.3
    llm_max_tokens: int = 2048
    embedding_model: str = "Qwen/Qwen3-Embedding-8B"
    embedding_dimensions: int = 1024
    semantic_similarity_threshold: float = 0.25
    prompt_version: str = "v1"
    context_strategy: str = "relevant"
    context_token_budget: int = 60000
    checkpoints_keep: int = 5
    judge_provider: str = "openai"
    judge_api_key: str = ""
    judge_model: str = ""
    judge_base_url: str = ""
    daily_spend_limit_usd: float = 25.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
