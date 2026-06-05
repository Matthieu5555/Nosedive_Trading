"""Unit tests for the pure translation layer of the IBKR market-data adapter: classifying feed
faults by IBKR error code, turning a ticker snapshot into broker-agnostic ticks, and mapping a
canonical instrument to an ib_async contract. The live event wiring is exercised by the
collector entrypoint against a gateway, not here. Skipped when ib_async is absent."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

pytest.importorskip("ib_async")

from algotrading.infra.universe.contracts import OptionContract, Right, Underlying  # noqa: E402
from algotrading.infra_ibkr.collectors.ibkr_adapter import (  # noqa: E402 - after importorskip
    IbkrMarketDataAdapter,
    classify_fault,
    reference_price,
    ticker_to_ticks,
    to_ib_contract,
)


def test_classify_fault_maps_entitlement_codes():
    assert classify_fault(354) == "entitlement"  # not subscribed, nothing displayed
    assert classify_fault(10168) == "entitlement"  # delayed data not available


def test_delayed_data_confirmation_is_not_a_fault():
    # 10167 = "Requested market data is not subscribed. Displaying delayed market data." This is
    # the expected path under reqMarketDataType(3) — delayed data IS flowing, so it must not be
    # flagged or counted as a fault.
    assert classify_fault(10167) == "other"


def test_classify_fault_maps_pacing_codes():
    assert classify_fault(420) == "pacing"
    assert classify_fault(100) == "pacing"


def test_classify_fault_defaults_to_other():
    assert classify_fault(2104) == "other"


@pytest.mark.parametrize(
    ("bid", "ask", "last", "close", "expected"),
    [
        (100.0, 102.0, None, None, 101.0),  # both sides present -> mid
        (None, 102.0, 50.0, 40.0, 50.0),  # one side missing -> last
        (100.0, 0.0, None, 40.0, 40.0),  # non-positive ask -> fall through to close
        (None, None, None, 40.0, 40.0),  # only prior close available
        (None, None, None, None, None),  # nothing usable
    ],
)
def test_reference_price_prefers_mid_then_last_then_close(bid, ask, last, close, expected):
    ticker = SimpleNamespace(bid=bid, ask=ask, last=last, close=close)
    assert reference_price(ticker) == expected


def test_ticker_to_ticks_emits_one_tick_per_quote_field():
    ticker = SimpleNamespace(bid=1.20, ask=1.30, last=1.25, close=1.10)
    ticks = ticker_to_ticks(ticker, instrument_key="UND:SPY:STK:SMART:USD", underlying="SPY")
    fields = {t.field_name: t.value for t in ticks}
    assert fields == {"bid": 1.20, "ask": 1.30, "last": 1.25, "close": 1.10}
    assert all(t.instrument_key == "UND:SPY:STK:SMART:USD" for t in ticks)
    assert all(t.underlying == "SPY" for t in ticks)


def test_ticker_to_ticks_filters_non_finite_and_absent_fields():
    # ib_async fills an absent quote with NaN; that is "no observation", not a null tick.
    ticker = SimpleNamespace(bid=1.20, ask=float("nan"), last=None, close=1.10)
    ticks = ticker_to_ticks(ticker, instrument_key="UND:SPY:STK:SMART:USD", underlying="SPY")
    assert {t.field_name for t in ticks} == {"bid", "close"}


def test_ticker_to_ticks_stamps_exchange_ts_and_contract_id():
    from datetime import UTC, datetime

    ts = datetime(2026, 6, 4, 14, 0, 0, tzinfo=UTC)
    ticker = SimpleNamespace(bid=1.20, ask=1.30, last=None, close=None, time=ts)
    ticks = ticker_to_ticks(
        ticker,
        instrument_key="UND:SPY:STK:SMART:USD",
        underlying="SPY",
        contract_id_broker="756733",
    )
    assert all(t.exchange_ts == ts for t in ticks)
    assert all(t.contract_id_broker == "756733" for t in ticks)


def test_ticker_to_ticks_naive_time_is_assumed_utc():
    from datetime import datetime

    ticker = SimpleNamespace(
        bid=1.20, ask=1.30, last=None, close=None, time=datetime(2026, 6, 4, 14)
    )
    ticks = ticker_to_ticks(ticker, instrument_key="UND:SPY:STK:SMART:USD", underlying="SPY")
    assert ticks[0].exchange_ts is not None and ticks[0].exchange_ts.tzinfo is not None


def test_to_ib_contract_builds_a_stock_for_an_underlying():
    contract = to_ib_contract(Underlying(symbol="SPY", exchange="SMART", currency="USD"))
    assert contract.symbol == "SPY"
    assert contract.exchange == "SMART"
    assert contract.currency == "USD"
    assert contract.secType == "STK"


class _Ticker:
    """Stand-in for an ib_async Ticker: hashable by identity (used as a dict key by the adapter)."""

    def __init__(self, contract):
        self.contract = contract
        self.bid = 1.0
        self.ask = 1.1
        self.last = 1.05
        self.close = 1.0


class _Event:
    """Stand-in for an ib_async event: only needs to accept handler registration via ``+=``."""

    def __iadd__(self, _handler):
        return self


class _FakeIB:
    """Minimal ib_async stand-in: the 1000 strike has no security definition (returns None)."""

    def __init__(self):
        self.pendingTickersEvent = _Event()
        self.errorEvent = _Event()
        self.subscribed = []

    def reqMarketDataType(self, _market_data_type):  # noqa: N802 — mirrors ib_async API naming.
        pass

    def qualifyContracts(self, contract):  # noqa: N802 — mirrors ib_async API naming.
        # ib_async returns a positional list with None where the broker has no definition.
        if getattr(contract, "strike", None) == 1000.0:
            return [None]
        return [contract]

    def reqMktData(self, contract):  # noqa: N802 — mirrors ib_async API naming.
        self.subscribed.append(contract)
        return _Ticker(contract)


def test_subscribe_skips_unqualifiable_contracts_and_records_them():
    # A discovery-superset strike that the broker cannot qualify must not abort the run: it is
    # skipped and recorded, and the remaining instruments still subscribe.
    ib = _FakeIB()
    adapter = IbkrMarketDataAdapter(ib)
    phantom = "OPT:SPY:OPT:20260619:C:1000:100:SMART:USD"
    good = "OPT:SPY:OPT:20260619:C:450:100:SMART:USD"
    underlying = "UND:SPY:STK:SMART:USD"

    adapter.subscribe([phantom, good, underlying])

    assert adapter.unresolved == [phantom]  # recorded, never silently dropped
    assert len(ib.subscribed) == 2  # only the two qualifiable contracts were subscribed
    subscribed_keys = {key for key, _underlying, _con_id in adapter._by_ticker.values()}
    assert subscribed_keys == {good, underlying}


def test_to_ib_contract_builds_an_option():
    option = OptionContract(
        symbol="SPY",
        expiry=date(2026, 6, 19),
        strike=Decimal("450"),
        right=Right.CALL,
        multiplier=100,
        exchange="SMART",
        currency="USD",
    )
    contract = to_ib_contract(option)
    assert contract.symbol == "SPY"
    assert contract.lastTradeDateOrContractMonth == "20260619"
    assert contract.strike == 450.0
    assert contract.right == "C"
    assert contract.multiplier == "100"
    assert contract.exchange == "SMART"
    assert contract.currency == "USD"
