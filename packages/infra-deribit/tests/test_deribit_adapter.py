"""Tests for DeribitMarketDataAdapter — synthetic payloads, real Deribit field names."""

from __future__ import annotations

from unittest.mock import MagicMock

from algotrading.infra.collectors import BrokerTick
from algotrading.infra_deribit.collectors.deribit_adapter import (
    DeribitMarketDataAdapter,
    _deribit_name_from_key,
    _ticks_from_ticker_data,
    _underlying_key,
)

_KEY = "OPT:BTC:OPT:20250627:C:100000:1:DERIBIT:USD"
_UND_KEY = "UND:BTC:CRYPTO:DERIBIT:USD"
_INDEX = 67000.0  # synthetic BTC/USD index price


def _ticker(**overrides) -> dict:
    """A Deribit option ticker payload with real field names (prices in BTC)."""
    data = {
        "instrument_name": "BTC-27JUN25-100000-C",
        "best_bid_price": 0.05,
        "best_ask_price": 0.06,
        "last_price": 0.055,
        "mark_price": 0.052,
        "index_price": _INDEX,
        "underlying_price": _INDEX,
    }
    data.update(overrides)
    for k in [k for k, v in overrides.items() if v is None]:
        data.pop(k, None)  # allow tests to drop a field with value=None
    return data


class TestTicksFromTickerData:
    def _option_ticks(self, data, index_price=None):
        return _ticks_from_ticker_data(
            data, instrument_key_str=_KEY, underlying="BTC", index_price=index_price
        )

    def test_prices_converted_to_usd(self):
        ticks = self._option_ticks(_ticker())
        bid = next(t for t in ticks if t.field_name == "bid" and t.instrument_key == _KEY)
        assert abs(bid.value - 0.05 * _INDEX) < 1e-6

    def test_standard_fields_emitted(self):
        names = {t.field_name for t in self._option_ticks(_ticker()) if t.instrument_key == _KEY}
        assert {"bid", "ask", "last", "mark_price"}.issubset(names)

    def test_frame_index_preferred_over_stale_subscribe_value(self):
        ticks = self._option_ticks(_ticker(), index_price=1.0)  # stale value must be ignored
        bid = next(t for t in ticks if t.field_name == "bid" and t.instrument_key == _KEY)
        assert abs(bid.value - 0.05 * _INDEX) < 1e-6

    def test_fallback_index_when_frame_missing(self):
        ticks = self._option_ticks(_ticker(index_price=None), index_price=_INDEX)
        bid = next(t for t in ticks if t.field_name == "bid" and t.instrument_key == _KEY)
        assert abs(bid.value - 0.05 * _INDEX) < 1e-6

    def test_mark_iv_emitted_as_fraction(self):
        iv = [t for t in self._option_ticks(_ticker(mark_iv=65.3)) if t.field_name == "mark_iv"]
        assert len(iv) == 1
        assert abs(iv[0].value - 0.653) < 1e-9

    def test_no_mark_iv_when_absent(self):
        assert not any(t.field_name == "mark_iv" for t in self._option_ticks(_ticker()))

    def test_underlying_spot_emitted(self):
        und = [t for t in self._option_ticks(_ticker()) if t.instrument_key == _UND_KEY]
        assert len(und) == 1
        assert und[0].field_name == "last"
        assert und[0].value == _INDEX  # underlying_price is already USD: no conversion
        assert und[0].underlying == "BTC"

    def test_none_value_for_missing_field(self):
        ticks = self._option_ticks(_ticker(best_bid_price=None))
        bid = next(t for t in ticks if t.field_name == "bid" and t.instrument_key == _KEY)
        assert bid.value is None


def test_underlying_key_helper():
    assert _underlying_key(_KEY) == _UND_KEY


def test_deribit_name_has_no_leading_zero_on_single_digit_day():
    # Deribit names single-digit days without a leading zero: "4JUN26", not "04JUN26".
    # A leading zero makes the WS subscription silently match nothing (empty result).
    assert (
        _deribit_name_from_key("OPT:BTC:OPT:20260604:C:65000:1:DERIBIT:USD") == "BTC-4JUN26-65000-C"
    )
    assert (
        _deribit_name_from_key("OPT:BTC:OPT:20261226:P:100000:1:DERIBIT:USD")
        == "BTC-26DEC26-100000-P"
    )


def test_converted_value_rounded_to_storage_scale():
    # base->USD multiplication introduces float noise beyond 6 places; values must be rounded
    # to fit the decimal128(38, 6) raw column, else the write is rejected.
    ticks = _ticks_from_ticker_data(
        _ticker(mark_price=0.052, index_price=66380.29, underlying_price=66380.29),
        instrument_key_str=_KEY,
        underlying="BTC",
        index_price=None,
    )
    mp = next(t for t in ticks if t.field_name == "mark_price" and t.instrument_key == _KEY)
    assert mp.value == round(0.052 * 66380.29, 6)
    assert len(str(mp.value).split(".")[-1]) <= 6


class TestDeribitMarketDataAdapterCallbacks:
    def test_on_ws_message_emits_usd_and_underlying_ticks(self, sample_ticker_ws_frame):
        adapter = DeribitMarketDataAdapter(MagicMock())
        received: list[BrokerTick] = []
        adapter.set_tick_callback(received.append)
        adapter._subscribed["BTC-27JUN25-100000-C"] = (_KEY, "BTC")
        adapter._index_prices["BTC"] = _INDEX
        adapter._on_ws_message(sample_ticker_ws_frame)

        names = {t.field_name for t in received}
        assert "bid" in names and "mark_iv" in names
        bid = next(t for t in received if t.field_name == "bid" and t.instrument_key == _KEY)
        assert abs(bid.value - 0.05 * _INDEX) < 1e-6
        assert any(t.instrument_key == _UND_KEY for t in received)

    def test_malformed_frame_no_exception(self):
        adapter = DeribitMarketDataAdapter(MagicMock())
        adapter._subscribed["BTC-27JUN25-100000-C"] = ("somekey", "BTC")
        adapter._index_prices["BTC"] = _INDEX
        adapter._on_ws_message({"method": "subscription", "params": {"data": None}})

    def test_unknown_instrument_silently_ignored(self, sample_ticker_ws_frame):
        adapter = DeribitMarketDataAdapter(MagicMock())
        received: list[BrokerTick] = []
        adapter.set_tick_callback(received.append)
        adapter._on_ws_message(sample_ticker_ws_frame)
        assert received == []
