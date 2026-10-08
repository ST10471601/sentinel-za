"""App settings, read from SENTINEL_* environment variables or a .env file."""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from sentinel.core.errors import ConfigurationError

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class BrokerType(StrEnum):
    """Which event broker to use."""

    LOCAL = "local"
    KAFKA = "kafka"


class LogFormat(StrEnum):
    """How log lines are written."""

    TEXT = "text"
    JSON = "json"


class Settings(BaseSettings):
    """Runtime settings. Every value has a default that works for local development."""

    model_config = SettingsConfigDict(
        env_prefix="SENTINEL_",
        env_file=".env",
        extra="ignore",
        frozen=True,
    )

    data_dir: Path = Path("data")  # generated data and local databases
    seed: int = Field(default=42, ge=0)  # same seed, same simulated data
    broker: BrokerType = BrokerType.LOCAL
    kafka_bootstrap_servers: str = "localhost:9092"
    db_url: str = "sqlite:///data/operational.db"
    log_level: LogLevel = "INFO"
    log_format: LogFormat = LogFormat.TEXT


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once and reuse them."""
    try:
        return Settings()
    except ValidationError as exc:
        raise ConfigurationError(f"invalid configuration:\n{exc}") from exc
