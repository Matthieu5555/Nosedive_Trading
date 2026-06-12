"""core.log: standalone JSON logging, idempotent handlers, and the configured-root deferral.

Expected payload shape is derived from the documented contract (one JSON object per line:
``ts``/``level``/``logger``/``message`` + non-reserved ``extra=`` keys), not from the
formatter's own output. Logger names are unique per test because stdlib loggers are
process-global.
"""

from __future__ import annotations

import io
import json
import logging
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from algotrading.core.log import HANDLER_MARKER, JsonFormatter, get_logger


def _unique(name: str) -> str:
    return f"{name}.{uuid.uuid4().hex[:8]}"


def _format_one(
    logger_name: str, level: int, message: str, extra: dict[str, object] | None = None
) -> dict[str, object]:
    """Render one record through JsonFormatter and parse the JSON line back."""
    logger = logging.getLogger(logger_name)
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    try:
        logger.log(level, message, extra=extra or {})
    finally:
        logger.removeHandler(handler)
    (line,) = buffer.getvalue().splitlines()
    parsed: dict[str, object] = json.loads(line)
    return parsed


def test_json_formatter_emits_the_documented_schema_with_extras() -> None:
    name = _unique("schema.module")
    record = _format_one(name, logging.INFO, "captured 12 events", extra={"count": 12})

    assert record["level"] == "INFO"
    assert record["logger"] == name
    assert record["message"] == "captured 12 events"
    assert record["count"] == 12
    ts = datetime.fromisoformat(str(record["ts"]))
    assert ts.tzinfo is not None and ts.astimezone(UTC).tzinfo is UTC


def test_json_formatter_renders_exc_info_as_traceback_text() -> None:
    logger = logging.getLogger(_unique("failing.module"))
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    try:
        raise ValueError("boom-3199")
    except ValueError:
        logger.exception("step failed")
    logger.removeHandler(handler)

    (line,) = buffer.getvalue().splitlines()
    record = json.loads(line)
    assert record["message"] == "step failed"
    assert "ValueError: boom-3199" in record["exc_info"]
    assert "Traceback" in record["exc_info"]


@pytest.fixture()
def _standalone_root() -> Iterator[None]:
    """Force the standalone mode: no marked root handler, regardless of what the
    process ran earlier (any app built in-suite may have called configure_logging)."""
    root = logging.getLogger()
    removed = [h for h in root.handlers if getattr(h, HANDLER_MARKER, False)]
    for handler in removed:
        root.removeHandler(handler)
    yield
    for handler in removed:
        root.addHandler(handler)


def test_get_logger_is_idempotent_and_does_not_propagate(_standalone_root: None) -> None:
    name = _unique("idempotent.module")
    logger = get_logger(name)
    again = get_logger(name)

    assert again is logger
    marked = [h for h in logger.handlers if getattr(h, HANDLER_MARKER, False)]
    assert len(marked) == 1  # a second call attached nothing
    assert logger.propagate is False  # own handler only — no double emission via root


@pytest.fixture()
def _marked_root_handler() -> Iterator[io.StringIO]:
    """Install a marked root handler, as the platform-wide configurator would."""
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(JsonFormatter())
    setattr(handler, HANDLER_MARKER, True)
    root = logging.getLogger()
    root.addHandler(handler)
    saved_level = root.level
    root.setLevel(logging.INFO)
    yield buffer
    root.removeHandler(handler)
    root.setLevel(saved_level)


def test_get_logger_defers_to_a_configured_root(_marked_root_handler: io.StringIO) -> None:
    name = _unique("deferring.module")
    logger = get_logger(name)

    assert logger.handlers == []  # nothing attached — the configured root owns rendering
    assert logger.propagate is True

    logger.info("routed via root", extra={"k": "v"})
    (line,) = _marked_root_handler.getvalue().splitlines()
    record = json.loads(line)
    assert record["logger"] == name
    assert record["message"] == "routed via root"
    assert record["k"] == "v"
