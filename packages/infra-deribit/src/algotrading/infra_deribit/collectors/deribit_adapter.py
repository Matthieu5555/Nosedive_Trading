"""Deribit market-data adapter: translate Deribit WebSocket ticker frames into BrokerTicks.

Implements the ``MarketDataAdapter`` protocol from ``algotrading.infra.collectors`` so the
broker-agnostic ``RawCollector`` can drive it without knowing anything about Deribit.

Each ticker update produces up to five ``BrokerTick`` events per instrument:
- ``bid``, ``ask``, ``last``, ``mark_price`` — standard quote fields, converted to USD
- ``mark_iv`` — Deribit's own implied-volatility estimate (used downstream by the QC check
  ``check_mark_iv_divergence`` to flag divergence vs the platform's recomputed IV)

Price conversion: Deribit BTC/ETH options are quoted in the base currency (BTC or ETH),
not USD. The adapter fetches the index price once at subscription time and multiplies every
price tick by it, so the canonical tick is always denominated in USD. ``mark_iv`` is
dimensionless and needs no conversion.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from algotrading.core.log import get_logger
from algotrading.infra.collectors import BrokerTick, FeedFault
from algotrading.infra.universe.contracts import (
    OptionContract,
    Underlying,
    instrument_key,
    parse_instrument_key,
)

from ..connectivity.deribit_transport import DeribitTransport
from ..connectivity.ws_listener import WebSocketListener

_log = get_logger(__name__)

# Deribit ticker price fields (base currency on the wire) -> canonical EAV field names.
# Deribit names the best quote `best_bid_price`/`best_ask_price` and the trade `last_price`.
_DERIBIT_PRICE_FIELDS: dict[str, str] = {
    "best_bid_price": "bid",
    "best_ask_price": "ask",
    "last_price": "last",
    "mark_price": "mark_price",
}

# The raw store column is decimal128(38, 6); the base->USD multiplication introduces binary
# float noise beyond 6 places, so values are rounded to the storable scale before emission.
_VALUE_DECIMALS = 6

# Deribit index name by underlying symbol.
_INDEX_NAMES: dict[str, str] = {
    "BTC": "btc_usd",
    "ETH": "eth_usd",
}


def _fetch_index_price(transport: DeribitTransport, symbol: str) -> float | None:
    """Fetch the current USD index price for ``symbol`` via REST.

    Returns ``None`` if the symbol is unknown or the request fails, in which case the
    caller must decide whether to abort or proceed without conversion.
    """
    index_name = _INDEX_NAMES.get(symbol.upper())
    if index_name is None:
        _log.warning("deribit_unknown_index", extra={"symbol": symbol})
        return None
    try:
        result = transport.get("/public/get_index_price", {"index_name": index_name})
        price = result.get("index_price")
        if price is None:
            _log.warning("deribit_index_price_missing", extra={"index_name": index_name})
            return None
        _log.info(
            "deribit_index_price_fetched",
            extra={"symbol": symbol, "index_name": index_name, "price": price},
        )
        return float(price)
    except Exception as exc:  # noqa: BLE001 — REST failure must not crash the adapter init
        _log.error(
            "deribit_index_price_error",
            extra={"symbol": symbol, "error": str(exc)},
        )
        return None


def _deribit_name_from_key(key: str) -> str:
    """Reconstruct the Deribit instrument name from a canonical instrument key.

    Canonical key example: ``OPT:BTC:OPT:20251226:C:100000:1:DERIBIT:USD``
    Deribit name example:  ``BTC-26DEC25-100000-C``
    """
    contract = parse_instrument_key(key)
    if not isinstance(contract, OptionContract):
        raise ValueError(f"not an option instrument key: {key!r}")
    # Deribit uses no leading zero on the day: "4JUN26", not "04JUN26".
    expiry_s = f"{contract.expiry.day}{contract.expiry.strftime('%b%y').upper()}"
    strike_s = (
        str(int(contract.strike))
        if contract.strike == contract.strike.to_integral_value()
        else str(contract.strike)
    )
    return f"{contract.symbol}-{expiry_s}-{strike_s}-{contract.right.value}"


def _underlying_key(option_key: str) -> str:
    """Canonical underlying key for an option key, e.g. ``UND:BTC:CRYPTO:DERIBIT:USD``."""
    contract = parse_instrument_key(option_key)
    return instrument_key(
        Underlying(
            symbol=contract.symbol,
            exchange=contract.exchange,
            currency=contract.currency,
            security_type="CRYPTO",
        )
    )


def _ticks_from_ticker_data(
    data: dict[str, Any],
    *,
    instrument_key_str: str,
    underlying: str,
    index_price: float | None,
) -> list[BrokerTick]:
    """Extract BrokerTick list from a Deribit ticker data payload.

    Option price fields are quoted in the base currency (BTC/ETH) and multiplied by the USD
    index price to yield USD. The per-frame ``index_price`` is preferred; the subscribe-time
    value is the fallback. When no index price is available, raw base-currency prices are
    emitted with a warning (downstream QC will reject them rather than passing them silently).
    The frame's ``underlying_price`` (already USD) is emitted as the underlying spot so the
    snapshot builder has a reference spot.
    """
    spot = data.get("index_price")
    spot = float(spot) if spot is not None else index_price
    if spot is None:
        _log.warning(
            "deribit_no_index_price_conversion",
            extra={"instrument_key": instrument_key_str},
        )
    ticks: list[BrokerTick] = []
    for deribit_field, field_name in _DERIBIT_PRICE_FIELDS.items():
        raw = data.get(deribit_field)
        if raw is not None and spot is not None:
            value: float | None = round(float(raw) * spot, _VALUE_DECIMALS)
        elif raw is not None:
            value = round(float(raw), _VALUE_DECIMALS)
        else:
            value = None
        ticks.append(
            BrokerTick(
                instrument_key=instrument_key_str,
                field_name=field_name,
                value=value,
                underlying=underlying,
                provider="DERIBIT",
            )
        )
    # mark_iv is dimensionless — no currency conversion needed.
    # Deribit expresses mark_iv as a percentage (e.g. 75.3 means 75.3% = 0.753).
    mark_iv = data.get("mark_iv")
    if mark_iv is not None:
        ticks.append(
            BrokerTick(
                instrument_key=instrument_key_str,
                field_name="mark_iv",
                value=round(float(mark_iv) / 100.0, _VALUE_DECIMALS),
                underlying=underlying,
                provider="DERIBIT",
            )
        )
    # Underlying spot (already in USD) -> the underlying instrument's `last`, so the snapshot
    # builder has a reference spot for forward/parity computation.
    under_px = data.get("underlying_price")
    under_px = float(under_px) if under_px is not None else spot
    if under_px is not None:
        ticks.append(
            BrokerTick(
                instrument_key=_underlying_key(instrument_key_str),
                field_name="last",
                value=round(under_px, _VALUE_DECIMALS),
                underlying=underlying,
                provider="DERIBIT",
            )
        )
    return ticks


class DeribitMarketDataAdapter:
    """Drive Deribit WebSocket subscriptions, surfacing ticks and feed faults to a collector.

    Implements the ``MarketDataAdapter`` protocol: ``subscribe``, ``set_tick_callback``,
    ``set_fault_callback``, ``unsubscribe_all``. The collector calls these; it never sees
    Deribit-specific types.

    Index prices (BTC/ETH → USD) are fetched once at ``subscribe`` time and reused for the
    session. A stale index price will slightly mis-price options but will not crash the pipeline;
    for intraday sessions the drift is negligible.
    """

    def __init__(self, transport: DeribitTransport) -> None:
        self._transport = transport
        self._tick_cb: Callable[[BrokerTick], None] | None = None
        self._fault_cb: Callable[[FeedFault], None] | None = None
        # Maps Deribit instrument name → (canonical_key, underlying_symbol)
        self._subscribed: dict[str, tuple[str, str]] = {}
        # Index prices fetched at subscribe time, keyed by underlying symbol.
        self._index_prices: dict[str, float | None] = {}
        self._listener: WebSocketListener | None = None

    def set_tick_callback(self, callback: Callable[[BrokerTick], None]) -> None:
        self._tick_cb = callback

    def set_fault_callback(self, callback: Callable[[FeedFault], None]) -> None:
        self._fault_cb = callback

    def subscribe(self, instrument_keys: Sequence[str]) -> None:
        """Open WebSocket subscriptions for each canonical instrument key.

        Fetches the USD index price for each unique underlying once via REST, then builds
        the channel list and starts the owned reconnecting WS listener thread.
        """
        channels: list[str] = []
        for key in instrument_keys:
            try:
                contract = parse_instrument_key(key)
                deribit_name = _deribit_name_from_key(key)
                self._subscribed[deribit_name] = (key, contract.symbol)
                channels.append(f"ticker.{deribit_name}.100ms")
                # Fetch index price once per unique underlying symbol.
                if contract.symbol not in self._index_prices:
                    self._index_prices[contract.symbol] = _fetch_index_price(
                        self._transport, contract.symbol
                    )
            except (ValueError, KeyError) as exc:
                _log.warning(
                    "deribit_subscribe_skip",
                    extra={"instrument_key": key, "reason": str(exc)},
                )
        if not channels:
            _log.warning("deribit_subscribe_empty")
            return
        # The transport builds a reconnecting listener (owned thread + stop event); the old
        # path scheduled an asyncio task on a loop that was never running, so no tick ever
        # flowed unless a caller happened to own a loop.
        self._listener = self._transport.ws_listener(
            channels, self._on_ws_message, on_fault=self._emit_fault
        )
        self._listener.start()

    def unsubscribe_all(self) -> None:
        """Stop the WebSocket listener thread and forget all subscriptions."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        self._subscribed.clear()
        self._index_prices.clear()

    def _emit_fault(self, reason: str) -> None:
        """Surface a WS connection fault to the collector (or log it when unwired)."""
        if self._fault_cb is not None:
            self._fault_cb(FeedFault(kind="other", code=None, message=reason, instrument_key=None))
        else:
            _log.warning("deribit_feed_fault_no_callback", extra={"reason": reason})

    def _on_ws_message(self, frame: dict[str, Any]) -> None:
        """Handle one Deribit notification frame."""
        try:
            params = frame.get("params", {})
            data = params.get("data", {})
            deribit_name: str = data.get("instrument_name", "")
            mapping = self._subscribed.get(deribit_name)
            if mapping is None:
                return
            key, underlying = mapping
            index_price = self._index_prices.get(underlying)
            for tick in _ticks_from_ticker_data(
                data,
                instrument_key_str=key,
                underlying=underlying,
                index_price=index_price,
            ):
                if self._tick_cb is not None:
                    self._tick_cb(tick)
        except (TypeError, AttributeError, KeyError, ValueError) as exc:
            _log.error("deribit_ws_parse_error", extra={"error": str(exc)})
            if self._fault_cb is not None:
                self._fault_cb(
                    FeedFault(kind="other", code=None, message=str(exc), instrument_key=None)
                )
