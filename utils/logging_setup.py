# Change log:
# Fri Nov 20 01:39:30 CET 2026 - Initial version: structured JSON logging, level from LOG_LEVEL in .env
# Fri Nov 20 02:30:00 CET 2026 - Load .env here so LOG_LEVEL applies regardless of import order
"""Structured (JSON) logging configured from the LOG_LEVEL environment variable."""
from __future__ import annotations

import json
import logging
import os

from dotenv import load_dotenv

load_dotenv()

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}
_STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON documents."""

    # Fri Nov 20 01:39:30 CET 2026 - Render a record as JSON including `extra` fields
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


# Fri Nov 20 01:39:30 CET 2026 - Return the configured log level name (default INFO)
def get_log_level() -> str:
    level = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
    return level if level in _VALID_LEVELS else "INFO"


# Fri Nov 20 01:39:30 CET 2026 - Configure the app logger once per process
def get_logger(name: str = "gcm_app") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(get_log_level())
    return logger


# Fri Nov 20 01:39:30 CET 2026 - True when DEBUG logging is enabled (detailed errors allowed in UI)
def debug_enabled() -> bool:
    return get_log_level() == "DEBUG"
