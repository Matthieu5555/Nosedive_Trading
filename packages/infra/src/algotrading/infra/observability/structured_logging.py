"""One platform-wide structured-logging configuration: every stream, one JSON schema.

Before this module, two parallel stacks ran in the same process (the 2026-06
maintainability audit, M8): ``core.log`` hand-rolled a stdlib JSON formatter, the
orchestration/actor/EOD path called ``structlog.get_logger`` with structlog never
configured (default pretty console output), and third-party loggers (httpx, uvicorn,
nautilus) fell through to logging's lastResort handler — three formats in one run log.

:func:`configure_logging` unifies them. After it runs, every record — structlog-native,
``core.log.get_logger``, plain stdlib, third-party — renders through ONE marked root
handler as one-line JSON with the load-bearing key schema ``core.log`` already emitted
(operational tooling greps/jq's these): ``ts`` (ISO-8601 UTC), ``level`` (uppercase),
``logger``, ``message``, plus caller extras as top-level keys and ``exc_info`` carrying
the rendered traceback.

Call it once, from a process entrypoint (the EOD runner, the BFF startup, a script's
``main``) — never from library code. Library code keeps using ``core.log.get_logger`` or
``structlog.get_logger`` unchanged: both join the stream automatically
(``core.log.get_logger`` detects the configured root via the shared
:data:`~algotrading.core.log.HANDLER_MARKER` and defers to it; per-logger handlers it
attached *before* configuration are swept into the root stream here). The configuration
lives in infra, not core, because infra is the package that declares structlog — core
stays dependency-free and owns only the marker contract.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import IO, Any

import structlog
from algotrading.core.log import HANDLER_MARKER

_Processor = Any  # structlog's processor protocol; Any keeps the chain declarations terse.
_EventDict = MutableMapping[str, Any]


def _uppercase_level(_logger: Any, _method_name: str, event_dict: _EventDict) -> _EventDict:
    """``INFO``, not ``info`` — the case core.log's JSON stream has always emitted."""
    level = event_dict.get("level")
    if isinstance(level, str):
        event_dict["level"] = level.upper()
    return event_dict


def _rename_exception_to_exc_info(
    _logger: Any, _method_name: str, event_dict: _EventDict
) -> _EventDict:
    """Keep the legacy key: core.log put the rendered traceback under ``exc_info``."""
    if "exception" in event_dict:
        event_dict["exc_info"] = event_dict.pop("exception")
    return event_dict


def _sweep_per_logger_handlers() -> None:
    """Fold loggers created by ``core.log.get_logger`` *before* configuration into the root.

    Each of those carries its own marked JSON handler with ``propagate=False``; once the
    configured root owns rendering, the per-logger handler would double-emit, so it is
    removed and propagation restored.
    """
    for name in list(logging.root.manager.loggerDict):
        logger = logging.root.manager.loggerDict[name]
        if not isinstance(logger, logging.Logger):
            continue  # a PlaceHolder for a dotted prefix, not a real logger
        marked = [h for h in logger.handlers if getattr(h, HANDLER_MARKER, False)]
        for handler in marked:
            logger.removeHandler(handler)
        if marked:
            logger.propagate = True


def configure_logging(level: int = logging.INFO, *, stream: IO[str] | None = None) -> None:
    """Route every logging stream through one root JSON handler. Call once per process.

    ``level`` is the root threshold (third-party loggers inherit it); ``stream`` defaults
    to stderr — where core.log has always written — and is injectable for tests.
    Idempotent: re-running replaces the previously installed marked root handler instead
    of stacking a second one.
    """
    # Shared chain: runs at log-call time for structlog-native events and (as the foreign
    # pre-chain) at format time for stdlib records. Produces the pinned key schema.
    shared_processors: list[_Processor] = [
        structlog.stdlib.add_logger_name,  # "logger"
        structlog.stdlib.add_log_level,  # "level" (lowercase, fixed next)
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
            # Lift stdlib `extra={...}` keys into the payload — core.log's JsonFormatter did.
            structlog.stdlib.ExtraAdder(),
        ],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,  # renders any exc_info -> "exception"
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
