"""B → A seam: B's contracts round-trip through A's store and A rejects malformed ones.

The architecture's bet is that A's typed contracts are the only objects crossing a
workstream line, so B (the consumer) owns the test proving its objects survive A's
write/read path and that A's write-ahead validation refuses a malformed one — checked
now, by B, not weeks later in E's integration (per ``tasks/TESTING.md``). Both contracts
B produces are covered, each with a happy round-trip and at least one malformed instance.
"""

from __future__ import annotations

import dataclasses
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from collectors import normalize_tick
from connectivity import BrokerTick
from contracts import ContractValidationError, InstrumentMaster, RawMarketEvent
from storage import ParquetStore
from universe import build_instrument_masters, resolve_chain

_TRADE_DATE = date(2026, 6, 1)
_T0 = datetime(2026, 6, 1, 13, 30, tzinfo=UTC)

_CHAIN_ROWS: list[dict[str, object]] = [
    {"conId": "u-AAPL", "symbol": "AAPL", "secType": "STK", "exchange": "SMART",
     "currency": "USD", "multiplier": 1},
    {"conId": "o-AAPL-C-100", "symbol": "AAPL", "secType": "OPT", "exchange": "SMART",
     "currency": "USD", "multiplier": 100, "expiry": "20260619", "strike": 100, "right": "C"},
]


def _instrument_master() -> InstrumentMaster:
    # Produced by B's universe pipeline, exactly as it would be materialized.
    return build_instrument_masters(resolve_chain(_CHAIN_ROWS), _TRADE_DATE)[0]


def _raw_market_event() -> RawMarketEvent:
    # Produced by B's collector normalization, exactly as it would be persisted.
    return normalize_tick(
        BrokerTick("o-AAPL-C-100", "bid", 5.25, sequence=1, exchange_ts=_T0),
        instrument_key="AAPL|OPT|SMART|USD|100|o-AAPL-C-100|2026-06-19|100|C",
        underlying="AAPL",
        session_id="sess-1",
        trade_date=_TRADE_DATE,
        receipt_ts=_T0 + timedelta(seconds=1),
    )


# -- happy round-trips through A's adapter ----------------------------------


def test_instrument_master_round_trips_through_a_storage(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    master = _instrument_master()
    store.write("instrument_master", [master])
    assert store.read("instrument_master") == [master]


def test_raw_market_event_round_trips_through_a_storage(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    event = _raw_market_event()
    store.write("raw_market_events", [event])
    assert store.read("raw_market_events") == [event]


# -- A's write-ahead validation refuses malformed instances -----------------


def test_a_malformed_instrument_master_is_rejected_by_a_validation(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    # A null primary-key field must be refused at the write door, not silently stored.
    malformed = dataclasses.replace(_instrument_master(), instrument_key=None)  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError) as info:
        store.write("instrument_master", [malformed])
    assert info.value.field == "instrument_key"


def test_a_malformed_raw_market_event_is_rejected_by_a_validation(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    # A NaN value is not a number to coerce; A must reject it explicitly.
    malformed = dataclasses.replace(_raw_market_event(), value=math.nan)
    with pytest.raises(ContractValidationError) as info:
        store.write("raw_market_events", [malformed])
    assert info.value.field == "value"


def test_a_naive_timestamp_raw_event_is_rejected_by_a_validation(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    naive = datetime(2026, 6, 1, 13, 30)  # no tzinfo
    malformed = dataclasses.replace(_raw_market_event(), canonical_ts=naive)
    with pytest.raises(ContractValidationError) as info:
        store.write("raw_market_events", [malformed])
    assert info.value.field == "canonical_ts"
