"""Application configuration, read once from the environment."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Absent secrets are tolerated until a provider is built."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str | None = None
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.0
    embedding_model: str = "text-embedding-3-small"
    embedding_backend: Literal["auto", "local", "openai"] = "auto"
    """Which embedder the index is built and queried with.

    Pinned separately from the chat key on purpose: the embedder is part of the index's
    identity, so adding an API key must not silently change it under a built index."""

    router_confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    retrieval_confidence_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    """Not yet calibrated. Measured on the local fallback embedder, in-domain queries score
    0.59-0.70 and out-of-domain ones 0.50-0.55, so this signal cannot carry out-of-domain
    rejection: that is the guardrail's job. This threshold only catches in-domain questions
    the corpus does not cover, and it must be re-measured against whichever embedder is
    configured."""
    max_hops: int = Field(default=2, ge=1)

    index_path: str = "data/processed/chroma"
    collection_name: str = "getnet_kb"
    retrieval_top_k: int = 5


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
