import json
import logging
import sys

import pytest

from sentinel.core.config import LogFormat
from sentinel.core.logs import JsonFormatter, configure_logging


def _record(message: str, **extra: object) -> logging.LogRecord:
    record = logging.makeLogRecord({"name": "test.logger", "levelname": "INFO", "msg": message})
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_includes_core_fields_and_extras() -> None:
    line = JsonFormatter().format(_record("Batch written", rows=500))
    payload = json.loads(line)
    assert payload["message"] == "Batch written"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.logger"
    assert payload["rows"] == 500
    assert "timestamp" in payload


def test_json_formatter_includes_exceptions() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.makeLogRecord({"msg": "failed"})
        record.exc_info = sys.exc_info()
    payload = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in payload["exception"]


@pytest.mark.parametrize(
    ("log_format", "formatter_type"),
    [(LogFormat.JSON, JsonFormatter), (LogFormat.TEXT, logging.Formatter)],
)
def test_configure_logging_installs_one_handler(
    log_format: LogFormat, formatter_type: type[logging.Formatter]
) -> None:
    configure_logging("DEBUG", log_format)
    configure_logging("DEBUG", log_format)  # calling twice must not duplicate handlers
    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert type(root.handlers[0].formatter) is formatter_type
    assert root.level == logging.DEBUG
