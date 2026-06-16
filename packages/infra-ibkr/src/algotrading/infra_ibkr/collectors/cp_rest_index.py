from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..connectivity.cp_rest_transport import SupportsRestGet
from .cp_rest_wire import SecdefSearchRow, parse_secdef_search_rows


class IndexConidError(Exception):
    pass


_INDEX_SEC_TYPE = "IND"


def _routes_index_on_exchange(row: SecdefSearchRow, exchange: str) -> bool:
    wanted = exchange.strip().upper()
    for section in row.sections:
        if section.sec_type.upper() != _INDEX_SEC_TYPE:
            continue
        venues = section.exchange.upper()
        listed = {v.strip() for chunk in venues.split(";") for v in chunk.split(",")}
        if wanted in listed:
            return True
    return False


def parse_index_conid(results: object, *, symbol: str, exchange: str) -> int:
    if not isinstance(results, Sequence):
        raise IndexConidError(f"secdef search for index {symbol!r} returned no list: {results!r}")
    symbol_matches = [
        row for row in parse_secdef_search_rows(results) if row.symbol.upper() == symbol.upper()
    ]
    for row in symbol_matches:
        if _routes_index_on_exchange(row, exchange) and row.conid is not None:
            return row.conid
    if (
        len(symbol_matches) == 1
        and not symbol_matches[0].sections
        and symbol_matches[0].conid is not None
    ):
        return symbol_matches[0].conid
    raise IndexConidError(
        f"secdef search for index {symbol!r} resolved no IND conid on exchange {exchange!r}"
    )


def resolve_index_conid(transport: SupportsRestGet, *, symbol: str, exchange: str) -> int:
    results = transport.get(
        "/iserver/secdef/search", params={"symbol": symbol, "secType": _INDEX_SEC_TYPE}
    )
    return parse_index_conid(results, symbol=symbol, exchange=exchange)


@dataclass(frozen=True, slots=True)
class ResolvedIndex:

    conid: int
    option_months: tuple[str, ...]


def parse_option_months(results: object, *, symbol: str) -> tuple[str, ...]:
    for row in parse_secdef_search_rows(results):
        if row.symbol.upper() != symbol.upper():
            continue
        for section in row.sections:
            if section.sec_type.upper() != "OPT":
                continue
            seen: list[str] = []
            for token in section.months.replace(",", ";").split(";"):
                month = token.strip()
                if month and month not in seen:
                    seen.append(month)
            return tuple(seen)
    return ()


def resolve_index(transport: SupportsRestGet, *, symbol: str, exchange: str) -> ResolvedIndex:
    results = transport.get(
        "/iserver/secdef/search", params={"symbol": symbol, "secType": _INDEX_SEC_TYPE}
    )
    conid = parse_index_conid(results, symbol=symbol, exchange=exchange)
    return ResolvedIndex(conid=conid, option_months=parse_option_months(results, symbol=symbol))


@dataclass(frozen=True, slots=True)
class OptionListing:
    """The option months a conid lists, plus the venue they list on.

    The venue matters: a constituent's options rarely list on the index exchange
    (Spanish names route to MEFFRV, Belgian to BELFOX, German/French to EUREX),
    and ``/secdef/strikes`` returns nothing unless asked on the right one.
    """

    months: tuple[str, ...]
    exchange: str | None


def _first_venue(venues: str) -> str | None:
    for chunk in venues.replace(",", ";").split(";"):
        venue = chunk.strip()
        if venue:
            return venue
    return None


def parse_option_listing_by_conid(results: object, *, conid: int) -> OptionListing:
    for row in parse_secdef_search_rows(results):
        if row.conid != conid:
            continue
        for section in row.sections:
            if section.sec_type.upper() != "OPT":
                continue
            seen: list[str] = []
            for token in section.months.replace(",", ";").split(";"):
                month = token.strip()
                if month and month not in seen:
                    seen.append(month)
            return OptionListing(months=tuple(seen), exchange=_first_venue(section.exchange))
    return OptionListing(months=(), exchange=None)


def parse_option_months_by_conid(results: object, *, conid: int) -> tuple[str, ...]:
    return parse_option_listing_by_conid(results, conid=conid).months


def option_listing_for_conid(
    transport: SupportsRestGet, *, symbol: str, conid: int
) -> OptionListing:
    results = transport.get("/iserver/secdef/search", params={"symbol": symbol})
    return parse_option_listing_by_conid(results, conid=conid)


def option_months_for_conid(
    transport: SupportsRestGet, *, symbol: str, conid: int
) -> tuple[str, ...]:
    return option_listing_for_conid(transport, symbol=symbol, conid=conid).months
