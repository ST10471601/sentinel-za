"""Time helpers. All timestamps are timezone-aware UTC."""

from datetime import UTC, datetime, timedelta, timezone

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
