"""Exception types raised by Sentinel ZA."""


class SentinelError(Exception):
    """Base class for all project errors."""


class ConfigurationError(SentinelError):
    """Settings are missing or invalid."""


class DataValidationError(SentinelError):
    """Data breaks its schema or a business rule."""


class SimulationError(SentinelError):
    """The simulator cannot produce data with the given settings."""
