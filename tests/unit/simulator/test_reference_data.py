import logging
from datetime import UTC, datetime

import pytest

from sentinel.core.datetimes import SAST
from sentinel.core.errors import DataValidationError
from sentinel.simulator.reference_data import ReferenceData, generate_reference_data

SMALL = 200


def test_same_seed_gives_identical_data() -> None:
    assert generate_reference_data(1, SMALL) == generate_reference_data(1, SMALL)


def test_different_seed_gives_different_data() -> None:
    first, second = generate_reference_data(1, SMALL), generate_reference_data(2, SMALL)
    assert first.customers != second.customers
    assert first.merchants != second.merchants


def test_tables_use_separate_random_streams() -> None:
    # Merchants don't depend on customers, so the customer count must not change them.
    assert generate_reference_data(1, SMALL).merchants == generate_reference_data(1, 50).merchants


def test_tables_are_listed_in_a_fixed_order(reference_data: ReferenceData) -> None:
    tables = reference_data.tables()
    assert list(tables) == ["customer", "account", "card", "device", "customer_device", "merchant"]
    assert tables["customer"] == reference_data.customers


def test_simulation_start_in_another_timezone_is_converted_to_utc() -> None:
    sast_midnight = datetime(2026, 1, 1, tzinfo=SAST)
    utc_equivalent = datetime(2025, 12, 31, 22, 0, tzinfo=UTC)
    assert generate_reference_data(1, SMALL, sast_midnight) == generate_reference_data(
        1, SMALL, utc_equivalent
    )


def test_naive_simulation_start_is_rejected() -> None:
    with pytest.raises(DataValidationError):
        generate_reference_data(1, SMALL, datetime(2026, 1, 1))  # noqa: DTZ001 - deliberately naive


def test_row_counts_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="sentinel.simulator.reference_data"):
        generate_reference_data(1, SMALL)
    record = caplog.records[-1]
    assert record.getMessage() == "generated reference data"
    assert record.__dict__["customer"] == SMALL
