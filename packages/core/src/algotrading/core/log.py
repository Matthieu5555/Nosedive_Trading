from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

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
    "ts",
    "level",
    "logger",
}

HANDLER_MARKER = "_algotrading_handler"


class JsonFormatter(logging.Formatter):

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


def _root_is_configured() -> bool:
    return any(
        getattr(handler, HANDLER_MARKER, False) for handler in logging.getLogger().handlers
    )


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if _root_is_configured():
        if logger.level == logging.NOTSET:
            logger.setLevel(level)
        return logger
    already = any(getattr(h, HANDLER_MARKER, False) for h in logger.handlers)
    if not already:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JsonFormatter())
        setattr(handler, HANDLER_MARKER, True)
        logger.addHandler(handler)
        logger.setLevel(level)
        logger.propagate = False
    return logger
