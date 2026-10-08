import pytest
from typer.testing import CliRunner

from sentinel import __version__
from sentinel.cli import CONFIG_ERROR_EXIT_CODE, app

runner = CliRunner()


def test_version_prints_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_config_shows_effective_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTINEL_SEED", "7")
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    assert "seed = 7" in result.output


def test_invalid_configuration_exits_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTINEL_LOG_LEVEL", "LOUD")
    result = runner.invoke(app, ["version"])
    assert result.exit_code == CONFIG_ERROR_EXIT_CODE
    assert "invalid configuration" in result.output
