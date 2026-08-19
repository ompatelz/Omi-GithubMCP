"""Application configuration and logging setup."""

from __future__ import annotations

import logging
import logging.config
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "RepoPilot MCP"
    log_level: LogLevel = "INFO"
    github_token: SecretStr | None = Field(
        default=None,
        description="GitHub REST API token.",
    )
    github_api_base_url: str = Field(
        default="https://api.github.com",
        description="Base URL for the GitHub REST API.",
    )
    github_request_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="Timeout for outbound GitHub REST API requests.",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()


def configure_logging(settings: Settings | None = None) -> None:
    """Configure process logging from application settings."""
    resolved = settings or get_settings()
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s %(levelname)s [%(name)s] %(message)s",
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                },
            },
            "root": {
                "handlers": ["console"],
                "level": resolved.log_level,
            },
        }
    )
