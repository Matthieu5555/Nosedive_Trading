"""History payload → ``DailyBar`` normalization (ADR 0031, Part C).

Independent oracle: a captured-style IBKR ``marketdata/history`` payload is read **by hand** in
the test (the OHLC values, the epoch-ms → trade_date mapping) and the normalizer's output is
asserted against those hand-read values — not against the normalizer round-tripping itself.
Edge cases mandatory (TESTING.md floor): an empty window and a single-bar window are both
exercised, and every malformed-row class raises a labeled error.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from algotrading.core.provenance import source_ref
from algotrading.infra.contracts import DailyBar
from algotrading.infra_ibkr.collectors.cp_rest_history_normalize import (
    HistoryNormalizeError,
    history_to_daily_bars,
    trade_date_of_bar,
)
from fixtures.records import make_stamp


def _provenance_for(_underlying: str, trade_date: date) -> object:
    return make_stamp((source_ref("raw_market_events", "ibkr-history", f"AAPL-{trade_date}"),))


# Two epoch-ms timestamps, computed by hand: 2026-06-04 and 2026-06-05 (UTC midnight session).
_T0_MS = int((datetime(2026, 6, 4, tzinfo=UTC) - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds() * 1000)
_T1_MS = int((datetime(2026, 6, 5, tzinfo=UTC) - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds() * 1000)

# A captured-style CP history payload (the shape the Web API returns).
_SAMPLE_PAYLOAD = {
    "symbol": "AAPL",
    "data": [
        {"t": _T0_MS, "o": 99.0, "h": 101.5, "l": 98.5, "c": 100.25, "v": 1_234_567},
        {"t": _T1_MS, "o": 100.25, "h": 102.0, "l": 99.75, "c": 101.5, "v": 2_345_678},
    ],
}


def _normalize(payload: dict[str, object]) -> tuple[DailyBar, ...]:
    return history_to_daily_bars(
        payload,
        provider="IBKR",
        underlying="AAPL",
        bar_type="1d-TRADES",
        source="cp-rest-history",
        provenance_for=lambda trade_date: _provenance_for("AAPL", trade_date),
    )


def test_trade_date_is_read_from_the_bars_own_timestamp() -> None:
    # The no-look-ahead anchor: the trade date comes from the bar's own epoch-ms 't', the UTC
    # calendar date of that instant — hand-computed here as 2026-06-04 / 2026-06-05.
    assert trade_date_of_bar(_T0_MS) == date(2026, 6, 4)
    assert trade_date_of_bar(_T1_MS) == date(2026, 6, 5)


def test_sample_payload_normalizes_to_expected_daily_bars() -> None:
    bars = _normalize(_SAMPLE_PAYLOAD)
    assert len(bars) == 2
    # Hand-read from _SAMPLE_PAYLOAD["data"][0].
    first = bars[0]
    assert first.provider == "IBKR"
    assert first.underlying == "AAPL"
    assert first.trade_date == date(2026, 6, 4)
    assert (first.open, first.high, first.low, first.close) == (99.0, 101.5, 98.5, 100.25)
    assert first.volume == 1_234_567.0
    assert first.bar_type == "1d-TRADES"
    # Hand-read from _SAMPLE_PAYLOAD["data"][1].
    assert bars[1].trade_date == date(2026, 6, 5)
    assert bars[1].close == 101.5


def test_empty_window_yields_no_bars() -> None:
    # A window with no history is a valid answer (empty), not an error (TESTING.md floor).
    assert _normalize({"symbol": "AAPL", "data": []}) == ()
    assert _normalize({"symbol": "AAPL"}) == ()  # no 'data' key at all


def test_single_bar_window() -> None:
    payload = {"symbol": "AAPL", "data": [{"t": _T0_MS, "o": 99.0, "h": 101.5, "l": 98.5, "c": 100.25, "v": 0}]}
    bars = _normalize(payload)
    assert len(bars) == 1
    assert bars[0].volume == 0.0  # zero volume is valid (non-negative), a one-bar window OK


def test_high_below_low_is_rejected() -> None:
    bad = {"symbol": "AAPL", "data": [{"t": _T0_MS, "o": 99.0, "h": 97.0, "l": 98.5, "c": 98.0, "v": 1}]}
    with pytest.raises(HistoryNormalizeError, match="high.*<.*low"):
        _normalize(bad)


def test_close_outside_range_is_rejected() -> None:
    bad = {"symbol": "AAPL", "data": [{"t": _T0_MS, "o": 99.0, "h": 101.5, "l": 98.5, "c": 200.0, "v": 1}]}
    with pytest.raises(HistoryNormalizeError, match="close.*outside"):
        _normalize(bad)


def test_negative_volume_is_rejected() -> None:
    bad = {"symbol": "AAPL", "data": [{"t": _T0_MS, "o": 99.0, "h": 101.5, "l": 98.5, "c": 100.0, "v": -1}]}
    with pytest.raises(HistoryNormalizeError, match="volume"):
        _normalize(bad)


def test_nan_field_is_rejected() -> None:
    bad = {"symbol": "AAPL", "data": [{"t": _T0_MS, "o": 99.0, "h": float("nan"), "l": 98.5, "c": 100.0, "v": 1}]}
    with pytest.raises(HistoryNormalizeError, match="finite"):
        _normalize(bad)


def test_missing_field_is_rejected() -> None:
    bad = {"symbol": "AAPL", "data": [{"t": _T0_MS, "o": 99.0, "h": 101.5, "l": 98.5, "v": 1}]}  # no close
    with pytest.raises(HistoryNormalizeError, match="missing field 'c'"):
        _normalize(bad)


def test_data_not_a_list_is_rejected() -> None:
    with pytest.raises(HistoryNormalizeError, match="must be a list"):
        _normalize({"symbol": "AAPL", "data": "not-a-list"})
