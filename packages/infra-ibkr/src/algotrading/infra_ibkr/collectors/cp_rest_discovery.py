"""IBKR Client Portal option-chain discovery (ADR 0024).

The Client Portal chain lookup is a **mandatory three-step sequence**, with a documented gotcha:

1. ``GET /iserver/secdef/search`` resolves the underlying conid and its option months — **the
   ``name`` field must be omitted**, or the response suppresses the strikes needed downstream;
2. ``GET /iserver/secdef/strikes`` returns the call/put strikes for one month;
3. ``GET /iserver/secdef/info`` returns the per-contract conid for one (month, strike, right).

Each step has a pure ``parse_*`` so the wire-shape handling is unit-tested without a live Gateway.
Output is the kept fork ``OptionContract`` model (the Saxo/Deribit universe), carrying the IBKR
conid as ``broker_contract_id`` so the adapter can map our instrument key ↔ conid. Selection
(``ChainSelection``: nearest months, spot-windowed strikes) is applied by the caller at wiring.
"""

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Any, Protocol

from algotrading.infra.universe import OptionContract, Right


class DiscoveryError(Exception):
    """A Client Portal contract-discovery response could not be resolved."""


class _SupportsGet(Protocol):
    def get(self, path: str, params: dict[str, Any] | None = None) -> Any: ...


def parse_search_conid(results: object, symbol: str) -> int:
    """Pick the underlying conid for ``symbol`` from a ``/secdef/search`` response."""
    if not isinstance(results, Sequence):
        raise DiscoveryError(f"search for {symbol!r} returned no list")
    for item in results:
        if isinstance(item, Mapping) and str(item.get("symbol", "")).upper() == symbol.upper():
            conid = item.get("conid")
            if conid is not None:
                return int(conid)
    raise DiscoveryError(f"search for {symbol!r} resolved no conid")


def parse_strikes(payload: object) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """A ``/secdef/strikes`` response → (call strikes, put strikes), each sorted ascending."""
    if not isinstance(payload, Mapping):
        raise DiscoveryError("strikes response is not an object")
    calls = tuple(sorted(float(s) for s in payload.get("call", ())))
    puts = tuple(sorted(float(s) for s in payload.get("put", ())))
    return calls, puts


def parse_info_contract(
    item: Mapping[str, object],
    *,
    symbol: str,
    exchange: str,
    currency: str,
    multiplier: int = 100,
) -> OptionContract:
    """One ``/secdef/info`` entry → a fork ``OptionContract`` (conid as broker id)."""
    try:
        maturity = str(item["maturityDate"])  # e.g. "20260116"
        expiry = date(int(maturity[0:4]), int(maturity[4:6]), int(maturity[6:8]))
        strike = Decimal(str(item["strike"]))
        right = Right.from_raw(str(item["right"]))
        conid = str(item["conid"])
    except (KeyError, ValueError, IndexError) as exc:
        raise DiscoveryError(f"malformed /secdef/info entry: {item!r}") from exc
    return OptionContract(
        symbol=symbol,
        expiry=expiry,
        strike=strike,
        right=right,
        multiplier=multiplier,
        exchange=exchange,
        currency=currency,
        broker_contract_id=conid,
        raw=dict(item),
    )


class CpRestDiscovery:
    """Drive the search → strikes → info sequence over an injected transport."""

    def __init__(
        self, transport: _SupportsGet, *, exchange: str = "SMART", currency: str = "USD"
    ) -> None:
        self._transport = transport
        self._exchange = exchange
        self._currency = currency

    def underlying_conid(self, symbol: str) -> int:
        """Resolve the underlying conid (``name`` deliberately omitted — see module docstring)."""
        results = self._transport.get(
            "/iserver/secdef/search", params={"symbol": symbol, "secType": "STK"}
        )
        return parse_search_conid(results, symbol)

    def strikes(self, conid: int, *, month: str) -> tuple[tuple[float, ...], tuple[float, ...]]:
        """Call/put strikes for one expiry month of the underlying ``conid``."""
        payload = self._transport.get(
            "/iserver/secdef/strikes",
            params={"conid": conid, "secType": "OPT", "month": month, "exchange": self._exchange},
        )
        return parse_strikes(payload)

    def contracts(
        self, conid: int, *, symbol: str, month: str, strike: float, right: str
    ) -> tuple[OptionContract, ...]:
        """The concrete option contract(s) for one (month, strike, right)."""
        results = self._transport.get(
            "/iserver/secdef/info",
            params={
                "conid": conid,
                "secType": "OPT",
                "month": month,
                "strike": strike,
                "right": right,
                "exchange": self._exchange,
            },
        )
        if not isinstance(results, Sequence):
            raise DiscoveryError(
                f"/secdef/info for {symbol} {month} {strike}{right} returned no list"
            )
        return tuple(
            parse_info_contract(
                item, symbol=symbol, exchange=self._exchange, currency=self._currency
            )
            for item in results
            if isinstance(item, Mapping)
        )
