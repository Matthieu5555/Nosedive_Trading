"""configure_logging unifies every logging stream onto one pinned JSON schema (audit M8).

The schema is the load-bearing contract operational tooling parses — derived here from the
documented ``core.log`` contract, not from the code under test: one JSON object per line with
``ts`` (ISO-8601 UTC), ``level`` (UPPERCASE), ``logger``, ``message``, caller key-values as
top-level keys, and ``exc_info`` carrying the rendered traceback. Streams covered:
structlog-native (the orchestration/EOD path), ``core.log.get_logger`` (created before AND
after configuration), and plain stdlib loggers (the third-party shape). State is restored
after each test — logging and structlog are process-global.
"""

from __future__ import annotations

import io
import json
import logging
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
import structlog
from algotrading.core.log import HANDLER_MARKER, get_logger
from algotrading.infra.observability import configure_logging

# The pinned schema keys (the documented core.log contract — see packages/core/README.md).
_SCHEMA_KEYS = {"ts", "level", "logger", "message"}


@pytest.fixture(autouse=True)
def _restore_global_logging_state() -> Iterator[None]:
    """logging + structlog are process-global; leave them as found."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    yield
    root.handlers = saved_handlers
    root.setLevel(saved_level)
    structlog.reset_defaults()


def _unique(name: str) -> str:
    """A fresh logger name per test — named stdlib loggers persist across tests."""
    return f"{name}.{uuid.uuid4().hex[:8]}"


def _lines(buffer: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in buffer.getvalue().splitlines()]


def test_structlog_native_emits_the_pinned_json_schema() -> None:
    buffer = io.StringIO()
    configure_logging(stream=buffer)

    name = _unique("orchestration.eod_run")
    structlog.get_logger(name).info("eod.stage_done", stage="reconstruct", count=3)

    (record,) = _lines(buffer)
    assert set(record) >= _SCHEMA_KEYS
    assert record["level"] == "INFO"  # uppercase — the case the JSON stream always carried
    assert record["logger"] == name
    assert record["message"] == "eod.stage_done"
    assert record["stage"] == "reconstruct"  # kwargs land as top-level keys
    assert record["count"] == 3
    # ts parses as an aware UTC ISO-8601 timestamp.
    ts = datetime.fromisoformat(str(record["ts"]))
    assert ts.tzinfo is not None and ts.astimezone(UTC).tzinfo is UTC


def test_stdlib_third_party_logger_joins_the_same_stream_and_schema() -> None:
    buffer = io.StringIO()
    configure_logging(stream=buffer)

    name = _unique("httpx")
    logging.getLogger(name).info("request sent %s", "GET", extra={"elapsed_ms": 12})

    (record,) = _lines(buffer)
    assert set(record) >= _SCHEMA_KEYS
    assert record["level"] == "INFO"
    assert record["logger"] == name
    assert record["message"] == "request sent GET"  # %-args interpolated
    assert record["elapsed_ms"] == 12  # stdlib extra= lifted into the payload


def test_core_log_logger_created_before_configure_is_swept_into_the_root_stream(
    capsys: pytest.CaptureFixture[str],
) -> None:
    name = _unique("early.module")
    early = get_logger(name)  # standalone: own marked handler, propagate=False

    buffer = io.StringIO()
    configure_logging(stream=buffer)

    early.info("early message", extra={"k": "v"})

    (record,) = _lines(buffer)  # exactly one emission — never doubled
    assert record["logger"] == name
    assert record["message"] == "early message"
    assert record["k"] == "v"
    assert capsys.readouterr().err == ""  # the old per-logger stderr handler is gone
    assert not any(getattr(h, HANDLER_MARKER, False) for h in early.handlers)
    assert early.propagate is True


def test_core_log_logger_created_after_configure_defers_to_the_root() -> None:
    buffer = io.StringIO()
    configure_logging(stream=buffer)

    name = _unique("late.module")
    late = get_logger(name)
    late.warning("late message", extra={"n": 1})

    assert late.handlers == []  # nothing attached — the configured root owns rendering
    (record,) = _lines(buffer)
    assert record["level"] == "WARNING"
    assert record["logger"] == name
    assert record["n"] == 1


@pytest.mark.parametrize("via", ["structlog", "stdlib"])
def test_exceptions_render_under_the_legacy_exc_info_key(via: str) -> None:
    buffer = io.StringIO()
    configure_logging(stream=buffer)

    name = _unique("failing.module")
    try:
        raise ValueError("boom-7741")
    except ValueError:
        if via == "structlog":
            structlog.get_logger(name).exception("step failed")
        else:
            logging.getLogger(name).error("step failed", exc_info=True)

    (record,) = _lines(buffer)
    assert record["level"] == "ERROR"
    assert record["message"] == "step failed"
    exc_text = str(record["exc_info"])
    assert "ValueError: boom-7741" in exc_text
    assert "Traceback" in exc_text


def test_reconfiguration_is_idempotent_one_marked_root_handler_one_emission() -> None:
    first = io.StringIO()
    configure_logging(stream=first)
    buffer = io.StringIO()
    configure_logging(stream=buffer)  # again — must replace, not stack

    root = logging.getLogger()
    marked = [h for h in root.handlers if getattr(h, HANDLER_MARKER, False)]
    assert len(marked) == 1

    name = _unique("once.module")
    structlog.get_logger(name).info("only once")
    assert len(_lines(buffer)) == 1
    assert first.getvalue() == ""  # the replaced handler received nothing


def test_root_level_gates_third_party_noise() -> None:
    buffer = io.StringIO()
    configure_logging(level=logging.WARNING, stream=buffer)

    logging.getLogger(_unique("chatty.lib")).info("debug-ish noise")
    logging.getLogger(_unique("chatty.lib")).warning("real problem")

    records = _lines(buffer)
    assert [r["message"] for r in records] == ["real problem"]
