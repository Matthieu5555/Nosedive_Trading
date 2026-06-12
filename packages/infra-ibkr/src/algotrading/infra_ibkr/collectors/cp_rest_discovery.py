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


# IBKR exchange codes by listing currency — the venue-consistency preference for resolving a
# constituent symbol to ITS index's market (broker vocabulary, like the endpoint paths; not an
# economic tunable). A symbol is globally ambiguous ('SAF' is Safran on SBF and Saratoga in the
# US; 'ITX' is Inditex on BM and Itaconix on LSE), and IBKR's "VALUE" venue is a dead/aggregated
# listing that serves no history. The preference order in :func:`parse_search_conid` is:
# currency-consistent venue → any non-VALUE stock row → any stock row → first symbol match.
# A currency absent here simply skips the first tier (the fallback tiers still apply).
_VENUES_BY_CURRENCY: dict[str, frozenset[str]] = {
    "USD": frozenset({"NYSE", "NASDAQ", "AMEX", "ARCA", "BATS"}),
    "EUR": frozenset({"IBIS", "FWB", "SBF", "AEB", "BVME", "BM", "HEX", "ENEXT.BE", "MTAA"}),
    "GBP": frozenset({"LSE"}),
    "CHF": frozenset({"EBS"}),
}
_DEAD_VENUE = "VALUE"


def _is_stock_row(item: Mapping[str, Any]) -> bool:
    """Whether a ``/secdef/search`` row is an equity listing.

    On the live wire the top-level ``secType`` is ``null``; the instrument kinds are in
    ``sections[].secType``. Both shapes are accepted (older fixtures carry a top-level
    ``secType`` and no sections).
    """
    if str(item.get("secType") or "").upper() == "STK":
        return True
    sections = item.get("sections")
    if not isinstance(sections, Sequence):
        return False
    return any(
        isinstance(section, Mapping) and str(section.get("secType") or "").upper() == "STK"
        for section in sections
    )


def parse_search_conid(results: object, symbol: str, *, currency: str | None = None) -> int:
    """Pick the underlying conid for ``symbol`` from a ``/secdef/search`` response.

    A bare symbol is globally ambiguous on this endpoint — IBKR returns non-equity roots
    (live: ``symbol=BA`` lists "BARLEY FUTURES ASX" *before* Boeing NYSE), dead "VALUE"
    listings, and foreign homonyms ahead of the listing the caller means. Preference order
    among the symbol-matching rows: an equity row on a venue consistent with ``currency``
    (:data:`_VENUES_BY_CURRENCY`), then any equity row not on the dead ``VALUE`` venue,
    then any equity row, then the first symbol match (rows with no sections on the wire).
    """
    if not isinstance(results, Sequence):
        raise DiscoveryError(f"search for {symbol!r} returned no list")
    matches = [
        item
        for item in results
        if isinstance(item, Mapping)
        and str(item.get("symbol", "")).upper() == symbol.upper()
        and item.get("conid") is not None
    ]
    stock_rows = [item for item in matches if _is_stock_row(item)]
    venues = _VENUES_BY_CURRENCY.get((currency or "").upper(), frozenset())
    for item in stock_rows:
        if str(item.get("description") or "").upper() in venues:
            return int(item["conid"])
    for item in stock_rows:
        if str(item.get("description") or "").upper() != _DEAD_VENUE:
            return int(item["conid"])
    if stock_rows:
        return int(stock_rows[0]["conid"])
    if matches:
        return int(matches[0]["conid"])
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
        """Resolve the underlying conid (``name`` deliberately omitted — see module docstring).

        The discovery's ``currency`` steers the venue preference: a constituent sweep built
        for an EUR index resolves 'SAF' to Safran on SBF, never the US homonym.
        """
        results = self._transport.get(
            "/iserver/secdef/search", params={"symbol": symbol, "secType": "STK"}
        )
        return parse_search_conid(results, symbol, currency=self._currency)

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
