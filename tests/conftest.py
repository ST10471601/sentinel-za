"""Shared pytest fixtures and configuration."""

import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from sentinel.core.config import get_settings

INTEGRATION_DIR = Path(__file__).parent / "integration"
SETTINGS_ENV_PREFIX = "SENTINEL_"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark every test under tests/integration as ``integration``."""
    for item in items:
        if INTEGRATION_DIR in item.path.parents:
            item.add_marker(pytest.mark.integration)


@pytest.fixture(autouse=True)
def clean_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Run each test with default settings: no .env file and no SENTINEL_* variables."""
    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if name.startswith(SETTINGS_ENV_PREFIX):
            monkeypatch.delenv(name)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def restore_root_logger() -> Iterator[None]:
    """Undo logging changes made by a test."""
    root = logging.getLogger()
    original_handlers, original_level = root.handlers[:], root.level
    yield
    root.handlers[:] = original_handlers
    root.setLevel(original_level)
