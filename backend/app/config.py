"""App settings, read from environment variables (and .env when present)."""

from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://lexintake:change-me@localhost:5432/lexintake"
    redis_url: str = "redis://localhost:6379/0"
    # NoDecode: read the raw string so a plain comma-separated list works.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    # Used from Phase 3. The model name always comes from the environment.
    anthropic_api_key: str = ""
    anthropic_model: str = ""

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
