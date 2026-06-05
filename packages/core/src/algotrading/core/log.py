"""Structured (JSON) logging for the AlgoTrading platform.

Every analytics object must be traceable; structured logs are the first link in that
chain. This module exposes a single entry point, :func:`get_logger`, that attaches a
JSON formatter emitting one object per line.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

# LogRecord attributes that are framework internals, not caller-supplied context.
# Anything on the record outside this set is treated as a structured "extra" field.
_RESERVED = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "taskName",
    "thread",
    "threadName",
    # Framework-owned payload keys — a caller "extra" must never overwrite these.
    "ts",
    "level",
    "logger",
}

# Marks a handler as installed by this module, so get_logger stays idempotent.
_MARKER = "_algotrading_handler"


class JsonFormatter(logging.Formatter):
    """Render a log record as a single-line JSON object with caller extras."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Return a logger that emits structured JSON to stderr.

    Calling this twice for the same name does not duplicate handlers.
    """
    logger = logging.getLogger(name)
    already = any(getattr(h, _MARKER, False) for h in logger.handlers)
    if not already:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JsonFormatter())
        setattr(handler, _MARKER, True)
        logger.addHandler(handler)
        logger.setLevel(level)
        # Own handler only — avoid double emission through the root logger.
        logger.propagate = False
    return logger
