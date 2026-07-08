from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Enterprise Knowledge Pipeline"
    environment: Literal["local", "test", "production"] = "local"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"])

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/knowledge_pipeline"
    database_pool_size: int = 8
    database_max_overflow: int = 12

    redis_url: str = "redis://localhost:6379/0"
    redis_cache_index: str = "semantic_cache"

    groq_api_key: SecretStr = SecretStr("")
    groq_base_url: str = "https://api.groq.com/openai/v1"
    embedding_model: str = "all-MiniLM-L6-v2"
    chat_model: str = "llama-3.3-70b-versatile"
    embedding_dimensions: int = 384
    cache_similarity_threshold: float = 0.95

    llm_input_cost_per_1k: float = 0.00015
    llm_output_cost_per_1k: float = 0.00060

    @property
    def sqlalchemy_database_url(self) -> str:
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
