from pathlib import Path

import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from sentinel import cli
from sentinel.cli import SIMULATION_ERROR_EXIT_CODE, app
from sentinel.core.errors import SimulationError

runner = CliRunner()

# Tests run in a temporary directory (see tests/conftest.py), so this is relative to it.
REFERENCE_DIR = Path("data/reference")


def test_simulate_reference_writes_parquet_files() -> None:
    result = runner.invoke(app, ["simulate", "reference", "--customers", "50", "--seed", "3"])

    assert result.exit_code == 0, result.output
    assert "customer" in result.output
    assert "50 rows" in result.output
    assert pq.read_metadata(REFERENCE_DIR / "customer.parquet").num_rows == 50


def test_seed_defaults_to_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTINEL_SEED", "5")
    result = runner.invoke(app, ["simulate", "reference", "--customers", "10"])

    assert result.exit_code == 0, result.output
    metadata = pq.read_schema(REFERENCE_DIR / "customer.parquet").metadata
    assert metadata[b"sentinel.seed"] == b"5"


def test_customer_count_must_be_positive() -> None:
    result = runner.invoke(app, ["simulate", "reference", "--customers", "0"])
    assert result.exit_code != 0
    assert not REFERENCE_DIR.exists()


def test_simulation_errors_exit_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_: object) -> None:
        raise SimulationError("not enough business names")

    monkeypatch.setattr(cli, "generate_reference_data", fail)
    result = runner.invoke(app, ["simulate", "reference"])

    assert result.exit_code == SIMULATION_ERROR_EXIT_CODE
    assert "not enough business names" in result.output
