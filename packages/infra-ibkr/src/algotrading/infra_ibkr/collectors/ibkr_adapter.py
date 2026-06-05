"""IBKR market-data adapter: the broker-specific edge that feeds the RawCollector.

Kept out of the package ``__init__`` so importing the collectors layer never drags in the broker
client — only code that actually subscribes to IBKR market data imports this module (requires the
optional ``ibkr`` dependency group). It turns ib_async callbacks into broker-agnostic ticks and
faults, the only place vendor types are allowed; everything downstream sees ``BrokerTick`` and
``FeedFault``. The pure translation helpers below carry that contract and are unit-tested without
a gateway; the live event wiring in the adapter class is exercised by the collector entrypoint.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime

from algotrading.core.log import get_logger
from algotrading.infra.collectors.collector import FeedFault
from algotrading.infra.collectors.normalize import BrokerTick
from algotrading.infra.universe import parse_instrument_key
from algotrading.infra.universe.contracts import OptionContract, Underlying
from ib_async import IB, Contract, Option, Stock, Ticker

_log = get_logger(__name__)

# IBKR error codes that mean "you are not entitled to this data" vs "you are asking too fast".
# Anything else is a connectivity or informational code the broker session handles, not the feed.
# Note 10167 ("displaying delayed market data") is deliberately NOT here: under reqMarketDataType
# delayed mode it confirms data IS flowing, so it is benign, not an entitlement failure.
_ENTITLEMENT_CODES = frozenset({354, 10168, 10197})
_PACING_CODES = frozenset({100, 162, 420})

# The core quote fields captured per ticker update; absence is recorded downstream as None.
_QUOTE_FIELDS = ("bid", "ask", "last", "close")


def classify_fault(code: int) -> str:
    """Classify an IBKR error code as ``"entitlement"``, ``"pacing"``, or ``"other"``."""
    if code in _ENTITLEMENT_CODES:
        return "entitlement"
    if code in _PACING_CODES:
        return "pacing"
    return "other"


def _ticker_exchange_ts(ticker: object) -> datetime | None:
    """The ticker quote time as an aware-UTC datetime (the originating observation), or None."""
    value = getattr(ticker, "time", None)
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def ticker_to_ticks(
    ticker: object,
    *,
    instrument_key: str,
    underlying: str,
    contract_id_broker: str | None = None,
) -> list[BrokerTick]:
    """Snapshot the present quote fields of a ticker into broker-agnostic ticks (one per field).

    Absent fields (``None`` or non-finite ``NaN``/``inf`` — ib_async's "no quote") are filtered at
    the source rather than emitted as null observations, mirroring the Saxo adapter. ``exchange_ts``
    is the ticker's quote time; ``contract_id_broker`` carries the qualified conId.
    """
    ts = _ticker_exchange_ts(ticker)
    ticks: list[BrokerTick] = []
    for field in _QUOTE_FIELDS:
        value = getattr(ticker, field, None)
        if value is None or (isinstance(value, float) and not math.isfinite(value)):
            continue
        ticks.append(
            BrokerTick(
                instrument_key=instrument_key,
                field_name=field,
                value=value,
                underlying=underlying,
                provider="IBKR",
                exchange_ts=ts,
                contract_id_broker=contract_id_broker,
            )
        )
    return ticks


def reference_price(ticker: object) -> float | None:
    """A usable reference spot from a ticker: bid/ask mid when both are present and positive,
    else last, else prior close; ``None`` when nothing usable is available."""
    bid = getattr(ticker, "bid", None)
    ask = getattr(ticker, "ask", None)
    if (
        bid is not None
        and ask is not None
        and math.isfinite(bid)
        and math.isfinite(ask)
        and bid > 0
        and ask > 0
    ):
        return (bid + ask) / 2.0
    for value in (getattr(ticker, "last", None), getattr(ticker, "close", None)):
        if value is not None and math.isfinite(value) and value > 0:
            return value
    return None


def to_ib_contract(instrument: Underlying | OptionContract) -> Contract:
    """Map a canonical instrument to the ib_async contract used to subscribe to its data."""
    if isinstance(instrument, Underlying):
        return Stock(instrument.symbol, instrument.exchange, instrument.currency)
    return Option(
        instrument.symbol,
        instrument.expiry.strftime("%Y%m%d"),
        float(instrument.strike),
        instrument.right.value,
        instrument.exchange,
        str(instrument.multiplier),
        instrument.currency,
    )


class IbkrMarketDataAdapter:
    """Drive ib_async market-data subscriptions, surfacing ticks and feed faults to a collector."""

    def __init__(self, ib: IB, *, market_data_type: int = 3) -> None:
        self._ib = ib
        self._market_data_type = market_data_type  # 3 = delayed, works without live entitlements
        self._tick_cb: Callable[[BrokerTick], None] | None = None
        self._fault_cb: Callable[[FeedFault], None] | None = None
        # ticker -> (instrument_key, underlying, contract_id_broker)
        self._by_ticker: dict[Ticker, tuple[str, str, str | None]] = {}
        # Instrument keys the broker has no security definition for (e.g. a strike from the
        # discovery superset that is not actually listed). Surfaced so coverage gaps are
        # attributable to "not listed" rather than a feed fault, never silently dropped.
        self.unresolved: list[str] = []
        ib.pendingTickersEvent += self._on_pending_tickers
        ib.errorEvent += self._on_error

    def set_tick_callback(self, callback: Callable[[BrokerTick], None]) -> None:
        self._tick_cb = callback

    def set_fault_callback(self, callback: Callable[[FeedFault], None]) -> None:
        self._fault_cb = callback

    def subscribe(self, instrument_keys: Sequence[str]) -> None:
        """Open a market-data subscription for each canonical instrument key."""
        self._ib.reqMarketDataType(self._market_data_type)
        for key in instrument_keys:
            instrument = parse_instrument_key(key)
            contract = to_ib_contract(instrument)
            # qualifyContracts returns a positional list with None where a contract has no
            # security definition. Subscribing the raw (unqualified) contract — or None —
            # would abort the whole run, so a contract that does not resolve is skipped and
            # recorded, and the remaining instruments still capture.
            qualified = self._ib.qualifyContracts(contract)
            resolved = qualified[0] if qualified else None
            if resolved is None:
                self.unresolved.append(key)
                _log.warning("skipping unqualifiable contract", extra={"instrument_key": key})
                continue
            ticker = self._ib.reqMktData(resolved)
            con_id = getattr(resolved, "conId", None)
            self._by_ticker[ticker] = (key, instrument.symbol, str(con_id) if con_id else None)

    def unsubscribe_all(self) -> None:
        for ticker in self._by_ticker:
            self._ib.cancelMktData(ticker.contract)
        self._by_ticker.clear()

    def _on_pending_tickers(self, tickers: Iterable[Ticker]) -> None:
        if self._tick_cb is None:
            return
        for ticker in tickers:
            mapping = self._by_ticker.get(ticker)
            if mapping is None:
                continue
            key, underlying, con_id = mapping
            for tick in ticker_to_ticks(
                ticker, instrument_key=key, underlying=underlying, contract_id_broker=con_id
            ):
                self._tick_cb(tick)

    def _on_error(
        self,
        req_id: int,
        error_code: int,
        error_string: str,
        contract: Contract | None = None,
    ) -> None:
        # Only feed-level faults belong here; connectivity/info codes are the session's concern.
        if self._fault_cb is None:
            return
        kind = classify_fault(error_code)
        if kind == "other":
            return
        instrument_key = self._instrument_key_for(contract)
        self._fault_cb(
            FeedFault(
                kind=kind, code=error_code, message=str(error_string), instrument_key=instrument_key
            )
        )

    def _instrument_key_for(self, contract: Contract | None) -> str | None:
        if contract is None:
            return None
        for ticker, (key, _underlying, _con_id) in self._by_ticker.items():
            if ticker.contract is contract or ticker.contract == contract:
                return key
        return None
