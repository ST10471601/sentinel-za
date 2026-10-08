"""Typed application settings.

Values come from environment variables prefixed ``SENTINEL_`` or from a ``.env``
file in the working directory (see ``.env.example``). Every setting has a safe
default for local development, so the project runs with no configuration.

Read settings through :func:`get_settings`; never read ``os.environ`` directly.
"""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from sentinel.core.errors import ConfigurationError

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class BrokerBackend(StrEnum):
    """Which event broker implementation to use."""

    LOCAL = "local"
    KAFKA = "kafka"


class LogFormat(StrEnum):
    """How log lines are rendered."""

    TEXT = "text"
    JSON = "json"


class Settings(BaseSettings):
    """All runtime configuration for Sentinel ZA."""

    model_config = SettingsConfigDict(
        env_prefix="SENTINEL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    data_dir: Path = Field(
        default=Path("data"),
        description="Root folder for generated data, Parquet files and local databases.",
    )
    seed: int = Field(default=42, ge=0, description="Random seed so simulations are reproducible.")
    broker: BrokerBackend = Field(
        default=BrokerBackend.LOCAL, description="Event broker implementation."
    )
    kafka_bootstrap_servers: str = Field(
        default="localhost:9092", description="Kafka brokers, used when broker is 'kafka'."
    )
    db_url: str = Field(
        default="sqlite:///data/operational.db",
        description="SQLAlchemy URL of the operational database.",
    )
    log_level: LogLevel = Field(default="INFO", description="Minimum log level.")
    log_format: LogFormat = Field(
        default=LogFormat.TEXT, description="'text' for humans, 'json' for machines."
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once and return the cached instance.

    Raises:
        ConfigurationError: If any setting is invalid.
    """
    try:
        return Settings()
    except ValidationError as exc:
        msg = f"Invalid configuration:\n{exc}"
        raise ConfigurationError(msg) from exc
