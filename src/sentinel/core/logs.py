"""Logging setup. Call configure_logging() once at startup, then use logging.getLogger(__name__)."""

import json
import logging
import sys
from datetime import UTC, datetime

from sentinel.core.config import LogFormat, LogLevel

TEXT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

# Fields every log record has. Anything else was passed in with `extra=`.
BUILTIN_FIELDS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Writes each log record as one line of JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Return the record as a JSON string."""
        data: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extras = {key: value for key, value in vars(record).items() if key not in BUILTIN_FIELDS}
        data.update(extras)

        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data, default=str)


def configure_logging(level: LogLevel, log_format: LogFormat) -> None:
    """Send logs to stderr. Safe to call more than once."""
    handler = logging.StreamHandler(sys.stderr)
    if log_format is LogFormat.JSON:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(TEXT_FORMAT))

    root = logging.getLogger()
    root.handlers = [handler]  # replace, so repeated calls don't duplicate output
    root.setLevel(level)
