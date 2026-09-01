"""Application configuration, read once from the environment."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Absent secrets are tolerated until a provider is built."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str | None = None
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.0
    embedding_model: str = "text-embedding-3-small"

    router_confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    retrieval_confidence_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    max_hops: int = Field(default=2, ge=1)

    index_path: str = "data/processed/chroma"
    collection_name: str = "getnet_kb"
    retrieval_top_k: int = 5


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
