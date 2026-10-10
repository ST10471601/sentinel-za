from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import TypeAdapter, ValidationError

from sentinel.core.datetimes import (
    SAST,
    UtcDatetime,
    add_months,
    add_years,
    ensure_utc,
    to_sast,
    utc_now,
)
from sentinel.core.errors import DataValidationError

UTC_DATETIME = TypeAdapter(UtcDatetime)


def test_utc_now_is_timezone_aware_utc() -> None:
    now = utc_now()
    assert now.tzinfo is UTC


def test_ensure_utc_converts_other_timezones() -> None:
    sast_noon = datetime(2026, 10, 8, 12, 0, tzinfo=SAST)
    assert ensure_utc(sast_noon) == datetime(2026, 10, 8, 10, 0, tzinfo=UTC)


def test_ensure_utc_rejects_naive_datetimes() -> None:
    naive = datetime(2026, 10, 8, 12, 0)  # noqa: DTZ001 - deliberately naive
    with pytest.raises(DataValidationError):
        ensure_utc(naive)


def test_to_sast_is_two_hours_ahead_of_utc() -> None:
    utc_time = datetime(2026, 10, 8, 22, 30, tzinfo=UTC)
    local = to_sast(utc_time)
    assert local.utcoffset() == timedelta(hours=2)
    assert (local.day, local.hour, local.minute) == (9, 0, 30)


def test_utc_datetime_field_stores_values_in_utc() -> None:
    value = UTC_DATETIME.validate_python(datetime(2026, 10, 8, 12, 0, tzinfo=SAST))
    assert value == datetime(2026, 10, 8, 10, 0, tzinfo=UTC)
    assert value.tzinfo is UTC


def test_utc_datetime_field_rejects_naive_datetimes() -> None:
    with pytest.raises(ValidationError):
        UTC_DATETIME.validate_python(datetime(2026, 10, 8, 12, 0))  # noqa: DTZ001 - deliberately naive


def test_add_years_moves_whole_years_both_ways() -> None:
    assert add_years(date(2026, 1, 15), -18) == date(2008, 1, 15)
    assert add_years(date(2026, 1, 15), 4) == date(2030, 1, 15)


def test_add_years_turns_leap_day_into_28_february() -> None:
    assert add_years(date(2024, 2, 29), 1) == date(2025, 2, 28)
    assert add_years(date(2024, 2, 29), 4) == date(2028, 2, 29)


@pytest.mark.parametrize(
    ("day", "months", "expected"),
    [
        (date(2026, 1, 1), 3, date(2026, 4, 1)),
        (date(2026, 11, 15), 2, date(2027, 1, 15)),
        (date(2026, 3, 10), -3, date(2025, 12, 10)),
        (date(2026, 1, 31), 1, date(2026, 2, 28)),  # past the month's end: last day
    ],
)
def test_add_months_moves_whole_months(day: date, months: int, expected: date) -> None:
    assert add_months(day, months) == expected
