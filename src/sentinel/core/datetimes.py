"""Time helpers. All timestamps are timezone-aware UTC."""

import calendar
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime

from sentinel.core.errors import DataValidationError

# SA has no daylight saving, so a fixed +2h offset is exact.
# It also avoids needing the tzdata package on Windows.
SAST = timezone(timedelta(hours=2), name="SAST")


def utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Convert to UTC. Naive datetimes are rejected because they are ambiguous."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise DataValidationError(f"datetime has no timezone: {value.isoformat()}")
    return value.astimezone(UTC)


def to_sast(value: datetime) -> datetime:
    """Convert to South African time for display."""
    return ensure_utc(value).astimezone(SAST)


def add_years(day: date, years: int) -> date:
    """Shift a date by whole years. 29 February becomes 28 February in non-leap years."""
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(year=day.year + years, day=28)


def add_months(day: date, months: int) -> date:
    """Shift a date by whole months. Days past the new month's end become its last day."""
    year, month_index = divmod(day.year * 12 + day.month - 1 + months, 12)
    month = month_index + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


# Model field type: rejects naive datetimes and stores the value in UTC.
UtcDatetime = Annotated[AwareDatetime, AfterValidator(ensure_utc)]
