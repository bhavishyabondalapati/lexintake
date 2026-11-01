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

    # Raw originals, stored by SHA-256 (a Docker volume in compose).
    blob_dir: str = "./.blobs"
    max_upload_mb: int = 50

    # Folder watcher: drop PDFs into inbox_dir and they get ingested.
    inbox_dir: str = "./inbox"
    watch_poll_s: float = 1.0
    # inotify events don't reliably cross Docker Desktop bind mounts, so compose turns this on.
    watch_force_polling: bool = False

    # Parsing: a document that keeps crashing the worker is marked failed after this many tries.
    parse_max_attempts: int = 3
    # "auto" picks mps on a Mac, cuda if present, else cpu. The worker image is CPU-only.
    parse_device: str = "auto"

    # Classification + extraction (Phase 3). Model names always come from the environment, never
    # from code: a cheap model classifies every document (and reads the small non-NDA field sets),
    # a stronger one extracts the full NDA fields.
    anthropic_api_key: str = ""
    anthropic_model_classify: str = ""
    anthropic_model_extract: str = ""
    # output_config.effort per step ("low" ... "max"); empty = don't send it.
    anthropic_effort_classify: str = "low"
    anthropic_effort_extract: str = "medium"
    # The SDK retries 408/409/429/5xx and connection errors this many times, with backoff.
    anthropic_max_retries: int = 4
    # Spend cap in USD: per run for `app.cli extract`, per UTC day for the extract worker.
    max_spend_usd: float = 1.0
    # Off: uploading a file never spends money by itself. On: a parsed document is queued for
    # extraction straight away.
    extract_on_parse: bool = False
    # Routing thresholds. The model's own confidence can only send a document to review, never
    # past a failed check (see app/extraction/route.py).
    classify_min_confidence: float = 0.8
    field_min_confidence: float = 0.8
    ocr_field_min_confidence: float = 0.9

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
