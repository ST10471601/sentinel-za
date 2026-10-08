from pathlib import Path

import pytest
from pydantic import ValidationError

from sentinel.core.config import BrokerBackend, LogFormat, get_settings
from sentinel.core.errors import ConfigurationError


def test_defaults_need_no_configuration() -> None:
    settings = get_settings()
    assert settings.seed == 42
    assert settings.broker is BrokerBackend.LOCAL
    assert settings.data_dir == Path("data")
    assert settings.log_format is LogFormat.TEXT


def test_environment_variables_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTINEL_SEED", "7")
    monkeypatch.setenv("SENTINEL_BROKER", "kafka")
    settings = get_settings()
    assert settings.seed == 7
    assert settings.broker is BrokerBackend.KAFKA


def test_dotenv_file_is_read(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SENTINEL_SEED=99\n", encoding="utf-8")
    assert get_settings().seed == 99


def test_invalid_values_raise_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTINEL_SEED", "-1")
    with pytest.raises(ConfigurationError, match="seed"):
        get_settings()


def test_settings_are_immutable() -> None:
    settings = get_settings()
    with pytest.raises(ValidationError):
        settings.seed = 1  # type: ignore[misc]


def test_settings_are_loaded_once() -> None:
    assert get_settings() is get_settings()
