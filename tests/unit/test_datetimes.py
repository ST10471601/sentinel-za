from datetime import UTC, datetime, timedelta

import pytest

from sentinel.core.datetimes import SAST, ensure_utc, to_sast, utc_now
from sentinel.core.errors import DataValidationError


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
