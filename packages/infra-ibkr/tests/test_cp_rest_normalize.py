"""The Client Portal market-data → RawMarketEvent seam (ADR 0024).

Pure and SDK-free → fully exercised in CI. Expected event ids are derived independently from the
documented ``content_event_id`` formula (SHA-256 of ``instrument_key \x1f field \x1f sequence``).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from algotrading.infra_ibkr.collectors.cp_rest_normalize import (
    REQUEST_FIELD_TAGS,
    snapshot_to_events,
)

_EXCHANGE = datetime(2026, 6, 4, 18, 29, 20, 115330, tzinfo=UTC)
_RECEIPT = datetime(2026, 6, 4, 18, 29, 20, 115587, tzinfo=UTC)
_IK = "OPT:SPY:OPT:20260626:C:758:100:SMART:USD"
_UNDERLYING = "SPY"
_SESSION = "ibkr-cp-spy-2026-06-04"


def _expected_event_id(field_name: str, sequence: int) -> str:
    payload = "\x1f".join((_IK, field_name, str(sequence)))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _events(row: dict[str, object], sequence: int = 7):
    return snapshot_to_events(
        row,
        instrument_key=_IK,
        underlying=_UNDERLYING,
        session_id=_SESSION,
        sequence=sequence,
        exchange_ts=_EXCHANGE,
        receipt_ts=_RECEIPT,
    )


def test_field_tags_map_to_named_events() -> None:
    # 84=bid, 86=ask, 88=bid_size, 85=ask_size, 31=last, 7059=last_size (CP field codes).
    row = {"84": "9.27", "86": "9.31", "88": "10", "85": "12", "31": "9.29", "7059": "100"}
    by_field = {e.field_name: e for e in _events(row)}
    assert set(by_field) == {"bid", "ask", "bid_size", "ask_size", "last", "last_size"}
    assert by_field["bid"].value == 9.27
    assert by_field["ask"].value == 9.31
    assert by_field["bid_size"].value == 10.0
    assert by_field["ask_size"].value == 12.0
    assert by_field["last"].value == 9.29
    assert by_field["last_size"].value == 100.0
    for event in by_field.values():
        assert event.instrument_key == _IK
        assert event.underlying == _UNDERLYING
        assert event.session_id == _SESSION
        assert event.exchange_ts == _EXCHANGE
        assert event.receipt_ts == _RECEIPT
        assert event.canonical_ts == _EXCHANGE
        assert event.trade_date == _EXCHANGE.date()
        assert event.event_id == _expected_event_id(event.field_name, 7)


def test_absent_and_sentinel_fields_are_dropped() -> None:
    # Only bid present; ask is the -1 no-value sentinel; others absent → one event.
    row = {"84": "9.27", "86": "-1"}
    events = _events(row)
    assert [e.field_name for e in events] == ["bid"]


def test_status_flag_prefix_is_stripped() -> None:
    # CP prefixes the last with 'C' when it is the prior close; the flag must be stripped.
    row = {"31": "C9.29"}
    events = _events(row)
    assert len(events) == 1
    assert events[0].field_name == "last"
    assert events[0].value == 9.29


def test_unknown_tags_are_ignored() -> None:
    row = {"84": "9.27", "55": "SPY", "6509": "RB"}  # 55=symbol, 6509=market-data availability
    assert [e.field_name for e in _events(row)] == ["bid"]


def test_idempotent_event_ids_per_sequence() -> None:
    row = {"84": "9.27", "86": "9.31"}
    first = _events(row, sequence=42)
    again = _events(row, sequence=42)
    later = _events(row, sequence=43)
    assert [e.event_id for e in first] == [e.event_id for e in again]
    assert set(e.event_id for e in first).isdisjoint(e.event_id for e in later)


def test_request_field_tags_are_the_mapped_ones() -> None:
    assert set(REQUEST_FIELD_TAGS) == {"31", "84", "86", "85", "88", "7059"}
