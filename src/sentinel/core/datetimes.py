"""Time helpers. Every timestamp in the system is a timezone-aware UTC datetime.

South Africa Standard Time is a fixed UTC+2 offset with no daylight saving, so a
fixed offset is used rather than a time-zone database lookup. This also avoids
needing the ``tzdata`` package on Windows.
"""

from datetime import UTC, datetime, timedelta, timezone

from sentinel.core.errors import DataValidationError

SAST = timezone(timedelta(hours=2), name="SAST")


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Return ``value`` converted to UTC.

    Raises:
        DataValidationError: If ``value`` is naive (has no timezone), because its
            meaning would be ambiguous.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        msg = f"Naive datetime is ambiguous; attach a timezone: {value.isoformat()}"
        raise DataValidationError(msg)
    return value.astimezone(UTC)


def to_sast(value: datetime) -> datetime:
    """Convert an aware datetime to South Africa Standard Time for display."""
    return ensure_utc(value).astimezone(SAST)
