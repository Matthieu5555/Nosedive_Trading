from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import IO, Any

import structlog
from algotrading.core.log import HANDLER_MARKER

_Processor = Any
_EventDict = MutableMapping[str, Any]


def _uppercase_level(_logger: Any, _method_name: str, event_dict: _EventDict) -> _EventDict:
    level = event_dict.get("level")
    if isinstance(level, str):
        event_dict["level"] = level.upper()
    return event_dict


def _rename_exception_to_exc_info(
    _logger: Any, _method_name: str, event_dict: _EventDict
) -> _EventDict:
    if "exception" in event_dict:
        event_dict["exc_info"] = event_dict.pop("exception")
    return event_dict


def _sweep_per_logger_handlers() -> None:
    for name in list(logging.root.manager.loggerDict):
        logger = logging.root.manager.loggerDict[name]
        if not isinstance(logger, logging.Logger):
            continue
        marked = [h for h in logger.handlers if getattr(h, HANDLER_MARKER, False)]
        for handler in marked:
            logger.removeHandler(handler)
        if marked:
            logger.propagate = True


def configure_logging(level: int = logging.INFO, *, stream: IO[str] | None = None) -> None:
    shared_processors: list[_Processor] = [
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        _uppercase_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
    ]

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            *shared_processors,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[
            *shared_processors,
            structlog.stdlib.ExtraAdder(),
        ],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            _rename_exception_to_exc_info,
            structlog.processors.EventRenamer("message"),
            structlog.processors.JSONRenderer(default=str),
        ],
    )

    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(formatter)
    setattr(handler, HANDLER_MARKER, True)

    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not getattr(h, HANDLER_MARKER, False)]
    root.addHandler(handler)
    root.setLevel(level)

    _sweep_per_logger_handlers()
