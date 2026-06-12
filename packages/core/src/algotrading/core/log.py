"""Structured (JSON) logging for the AlgoTrading platform.

Every analytics object must be traceable; structured logs are the first link in that
chain. This module exposes a single entry point, :func:`get_logger`, that attaches a
JSON formatter emitting one object per line.

Standalone by default, cooperative when configured: when the platform-wide logging
configuration has installed its handler on the root logger
(:func:`algotrading.infra.observability.configure_logging` — it marks the handler with
:data:`HANDLER_MARKER`), :func:`get_logger` attaches nothing and lets records propagate
into that one root stream instead, so a process never emits two formats. core stays
dependency-free: it only defines the marker; the structlog-based configurator lives in
infra and imports it from here.
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

# Marks a handler as installed by this platform's logging machinery — either by
# get_logger below (per-logger JSON handler, the standalone default) or by the
# platform-wide configure_logging (one root handler). One marker serves both: it keeps
# get_logger idempotent, and it lets get_logger detect a configured root and defer to it.
HANDLER_MARKER = "_algotrading_handler"


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


def _root_is_configured() -> bool:
    """Whether the platform-wide logging configuration owns the root logger."""
    return any(
        getattr(handler, HANDLER_MARKER, False) for handler in logging.getLogger().handlers
    )


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Return a logger that emits structured JSON to stderr.

    Calling this twice for the same name does not duplicate handlers. When the
    platform-wide configuration has installed its marked handler on the root logger,
    no per-logger handler is attached — records propagate into that one root stream
    (same JSON schema, one handler for the whole process).
    """
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
        # Own handler only — avoid double emission through the root logger.
        logger.propagate = False
    return logger
