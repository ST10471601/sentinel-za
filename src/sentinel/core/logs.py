"""Logging configuration.

Call :func:`configure_logging` once at startup (the CLI does this). Modules then
log with ``logging.getLogger(__name__)`` and pass structured fields via
``extra``, for example ``logger.info("Batch written", extra={"rows": 500})``.
Never use ``print`` for diagnostics.
"""

import json
import logging
import sys
from datetime import UTC, datetime

from sentinel.core.config import LogFormat, LogLevel

TEXT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

# Attributes every LogRecord has; anything else on a record came from ``extra``.
_STANDARD_RECORD_ATTRIBUTES = frozenset(vars(logging.makeLogRecord({}))) | {
    "message",
    "asctime",
}


class JsonFormatter(logging.Formatter):
    """Render each record as one JSON object per line, including ``extra`` fields."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialise ``record`` to a JSON string."""
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(
            {
                key: value
                for key, value in vars(record).items()
                if key not in _STANDARD_RECORD_ATTRIBUTES
            }
        )
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: LogLevel, log_format: LogFormat) -> None:
    """Send all log records to stderr at ``level`` in the chosen format.

    Safe to call more than once: existing root handlers are replaced.
    """
    handler = logging.StreamHandler(sys.stderr)
    formatter = JsonFormatter() if log_format is LogFormat.JSON else logging.Formatter(TEXT_FORMAT)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
