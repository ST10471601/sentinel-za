"""Project-wide exception hierarchy.

Catch ``SentinelError`` to handle any failure raised by this codebase, or a
subclass to handle one category. Add a new subclass only when callers need to
handle that category differently.
"""


class SentinelError(Exception):
    """Base class for every error raised by Sentinel ZA."""


class ConfigurationError(SentinelError):
    """Settings are missing, invalid or inconsistent."""


class DataValidationError(SentinelError):
    """Data does not match its expected schema or business rules."""
