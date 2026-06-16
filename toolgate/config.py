"""Runtime settings, read from the environment with defaults that work locally."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Everything the bridge needs to start, with no secrets baked in."""

    model_config = SettingsConfigDict(env_prefix="TOOLGATE_", env_file=".env", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://toolgate:toolgate@localhost:5432/toolgate",
        description="SQLAlchemy URL of the database that holds the audit table.",
    )
    tool_dir: str = Field(
        default="tools",
        description="Directory scanned for tool manifests at startup.",
    )
    call_timeout_s: float = Field(
        default=5.0,
        gt=0,
        description="Default timeout for one upstream call, in seconds.",
    )
    max_retries: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Retries after a 5xx or a connection error, per call.",
    )
    api_token: str = Field(
        default="dev-token",
        description="Shared token the caller sends in the X-API-Token header.",
    )
    openai_model: str = Field(
        default="gpt-4o-mini",
        description="Model name written into the generated tool definitions.",
    )
    upstream_base_url: str = Field(
        default="http://localhost:8080",
        description="Base URL that relative tool URLs in manifests are resolved against.",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, built once from the environment."""
    return Settings()
