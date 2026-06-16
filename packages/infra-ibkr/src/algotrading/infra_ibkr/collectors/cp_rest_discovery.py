from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal

from algotrading.infra.universe import OptionContract, Right
from pydantic import ValidationError

from ..connectivity.cp_rest_transport import SupportsRestGet
from .cp_rest_wire import (
    SecdefInfoRow,
    SecdefSearchRow,
    StrikesPayload,
    parse_secdef_search_rows,
)


class DiscoveryError(Exception):
    pass


_VENUES_BY_CURRENCY: dict[str, frozenset[str]] = {
    "USD": frozenset({"NYSE", "NASDAQ", "AMEX", "ARCA", "BATS"}),
    "EUR": frozenset({"IBIS", "FWB", "SBF", "AEB", "BVME", "BM", "HEX", "ENEXT.BE", "MTAA"}),
    "GBP": frozenset({"LSE"}),
    "CHF": frozenset({"EBS"}),
}
_DEAD_VENUE = "VALUE"


def _is_stock_row(row: SecdefSearchRow) -> bool:
    if row.sec_type.upper() == "STK":
        return True
    return any(section.sec_type.upper() == "STK" for section in row.sections)


def parse_search_conid(results: object, symbol: str, *, currency: str | None = None) -> int:
    if not isinstance(results, Sequence):
        raise DiscoveryError(f"search for {symbol!r} returned no list")
    matches = [
        row
        for row in parse_secdef_search_rows(results)
        if row.symbol.upper() == symbol.upper() and row.conid is not None
    ]
    stock_rows = [row for row in matches if _is_stock_row(row)]
    venues = _VENUES_BY_CURRENCY.get((currency or "").upper(), frozenset())
    for row in stock_rows:
        if row.description.upper() in venues and row.conid is not None:
            return row.conid
    for row in stock_rows:
        if row.description.upper() != _DEAD_VENUE and row.conid is not None:
            return row.conid
    if stock_rows and stock_rows[0].conid is not None:
        return stock_rows[0].conid
    if matches and matches[0].conid is not None:
        return matches[0].conid
    raise DiscoveryError(f"search for {symbol!r} resolved no conid")


def parse_strikes(payload: object) -> tuple[tuple[float, ...], tuple[float, ...]]:
    if not isinstance(payload, Mapping):
        raise DiscoveryError("strikes response is not an object")
    try:
        parsed = StrikesPayload.model_validate(payload)
    except ValidationError as exc:
        raise DiscoveryError(f"malformed /secdef/strikes payload: {payload!r}") from exc
    return tuple(sorted(parsed.call)), tuple(sorted(parsed.put))


def parse_info_contract(
    item: Mapping[str, object],
    *,
    symbol: str,
    exchange: str,
    currency: str,
    multiplier: int = 100,
) -> OptionContract:
    try:
        row = SecdefInfoRow.model_validate(item)
        maturity = row.maturity_date
        expiry = date(int(maturity[0:4]), int(maturity[4:6]), int(maturity[6:8]))
        strike = Decimal(row.strike)
        right = Right.from_raw(row.right)
        conid = row.conid
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

    def __init__(
        self, transport: SupportsRestGet, *, exchange: str = "SMART", currency: str = "USD"
    ) -> None:
        self._transport = transport
        self._exchange = exchange
        self._currency = currency

    def underlying_conid(self, symbol: str) -> int:
        results = self._transport.get(
            "/iserver/secdef/search", params={"symbol": symbol, "secType": "STK"}
        )
        return parse_search_conid(results, symbol, currency=self._currency)

    def strikes(self, conid: int, *, month: str) -> tuple[tuple[float, ...], tuple[float, ...]]:
        payload = self._transport.get(
            "/iserver/secdef/strikes",
            params={"conid": conid, "secType": "OPT", "month": month, "exchange": self._exchange},
        )
        return parse_strikes(payload)

    def contracts(
        self, conid: int, *, symbol: str, month: str, strike: float, right: str
    ) -> tuple[OptionContract, ...]:
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
