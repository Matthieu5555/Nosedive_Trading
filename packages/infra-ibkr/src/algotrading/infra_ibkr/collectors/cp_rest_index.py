"""Runtime index conid resolution from a symbol over CP REST (ADR 0024/0035).

The index registry (``configs/universe.yaml``, ADR 0035) carries an ``ibkr.conid`` per index,
but it is an *unverified placeholder* (``0``) for an index whose contract has not been hand-
verified — and a wrong conid silently qualifies the wrong contract. The live capture path does
not depend on that placeholder: it resolves each enabled index's conid at fire time from its
symbol via the CP REST secdef search, the same ``GET /iserver/secdef/search`` the option-chain
discovery (:mod:`.cp_rest_discovery`) drives, filtered to an **index** (``secType == "IND"``)
on the index's IBKR routing exchange (CBOE for SPX, EUREX for SX5E — the values the registry's
``ibkr.exchange`` already holds).

This is the seam that eliminates the ``conid: 0`` problem for the live path: the yaml conids
become unused, the symbol is resolved to the real contract id each run. Pure ``parse_*`` over
the typed wire rows (:mod:`.cp_rest_wire`) so the selection logic is unit-tested against a fake
search response — no live Gateway, no network.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..connectivity.cp_rest_transport import SupportsRestGet
from .cp_rest_wire import SecdefSearchRow, parse_secdef_search_rows


class IndexConidError(Exception):
    """An index conid could not be resolved from a secdef-search response — labeled, never None.

    Carries the symbol and the routing exchange so an unresolvable index (a wrong symbol, an
    exchange mismatch, an empty response) reads as a named capture failure rather than a silent
    fallback to a placeholder conid that would qualify the wrong contract.
    """


# IBKR's security type for an index (vs ``STK`` for a stock, ``OPT`` for an option). The search
# response lists, per matched symbol, the section types the symbol trades under; we keep the
# entry whose index section routes through the requested exchange.
_INDEX_SEC_TYPE = "IND"


def _routes_index_on_exchange(row: SecdefSearchRow, exchange: str) -> bool:
    """Whether a search row offers an ``IND`` section routed through ``exchange``.

    A CP secdef-search row carries a ``sections`` list, each section a ``{secType, exchange}``
    pair where ``exchange`` is a semicolon/comma-joined list of routing venues. The row matches
    when it has an ``IND`` section whose exchange list contains the requested routing exchange
    (CBOE/EUREX) — so a symbol that also lists as a stock or on another venue is not mistaken
    for the index we want.
    """
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
    """Pick the index conid for ``symbol`` on ``exchange`` from a ``/secdef/search`` response.

    Keeps the row whose ``symbol`` matches (case-insensitively) and which offers an ``IND``
    section routed through ``exchange`` (so the index contract is selected, never a same-symbol
    stock or a same-symbol index on a different venue). When exactly one symbol-matching row is
    returned and it carries no usable ``sections`` block, that row's conid is accepted (the
    response shape some endpoints return), so a thin-but-unambiguous response still resolves.
    A response that matches nothing raises a labeled :class:`IndexConidError`.
    """
    if not isinstance(results, Sequence):
        raise IndexConidError(f"secdef search for index {symbol!r} returned no list: {results!r}")
    symbol_matches = [
        row for row in parse_secdef_search_rows(results) if row.symbol.upper() == symbol.upper()
    ]
    # Prefer a row that explicitly routes the index on the requested exchange.
    for row in symbol_matches:
        if _routes_index_on_exchange(row, exchange) and row.conid is not None:
            return row.conid
    # Fall back to a single unambiguous symbol match with no sections block to filter on.
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
    """Resolve an index's IBKR conid from its symbol via ``GET /iserver/secdef/search``.

    The ``name`` field is deliberately omitted (the documented CP gotcha — including it
    suppresses parts of the response, see :mod:`.cp_rest_discovery`). The response is filtered
    to the ``IND`` section on the requested routing exchange and the matching conid returned.
    This is the runtime replacement for the registry's ``ibkr.conid`` placeholder on the live
    path. Raises a labeled :class:`IndexConidError` rather than guessing on an empty/ambiguous
    response.
    """
    results = transport.get(
        "/iserver/secdef/search", params={"symbol": symbol, "secType": _INDEX_SEC_TYPE}
    )
    return parse_index_conid(results, symbol=symbol, exchange=exchange)


@dataclass(frozen=True, slots=True)
class ResolvedIndex:
    """One index resolved from a secdef search: its conid and its listed option months.

    ``conid`` is the verified runtime contract id (the live-path replacement for the registry
    placeholder); ``option_months`` are the ``YYYYMM``-style month tokens the option chain lists
    under (e.g. ``("JUN26", "JUL26")``), read from the same search response's ``OPT`` section so
    the chain discovery does not fire a second search.
    """

    conid: int
    option_months: tuple[str, ...]


def parse_option_months(results: object, *, symbol: str) -> tuple[str, ...]:
    """The option-chain months listed for ``symbol`` in a ``/secdef/search`` response.

    The search row carries, under its ``OPT`` section, a ``months`` string of semicolon-joined
    month tokens (``"JUN26;JUL26;SEP26"``). Returns them in listed order, de-duplicated; an
    empty tuple when the symbol lists no option section (a name with no options — the caller
    degrades to a no-capture day, never a crash).
    """
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
    """Resolve an index's conid AND its listed option months from one ``/secdef/search`` call.

    A single search resolves both the index conid (filtered to the ``IND`` section on the
    routing exchange) and the option months (from the ``OPT`` section), so the close-capture
    chain discovery reuses one response rather than firing a second search. Raises a labeled
    :class:`IndexConidError` on a response that resolves no index conid.
    """
    results = transport.get(
        "/iserver/secdef/search", params={"symbol": symbol, "secType": _INDEX_SEC_TYPE}
    )
    conid = parse_index_conid(results, symbol=symbol, exchange=exchange)
    return ResolvedIndex(conid=conid, option_months=parse_option_months(results, symbol=symbol))


def parse_option_months_by_conid(results: object, *, conid: int) -> tuple[str, ...]:
    """The option-chain months for the row whose ``conid`` matches — a conid-keyed sibling.

    :func:`parse_option_months` matches a row by *symbol*, which a pinned constituent cannot use
    (the pin exists precisely because the bare ticker resolves ambiguously). When the underlying is
    already pinned to a unique conid, the month list must be read from the row that *is* that conid,
    not the row that shares a (shared) ticker. Same ``OPT``-section parsing, keyed on the conid;
    ``()`` when the conid lists no option section.
    """
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
            return tuple(seen)
    return ()


def option_months_for_conid(
    transport: SupportsRestGet, *, symbol: str, conid: int
) -> tuple[str, ...]:
    """The option-chain months a *known-conid* underlying lists, via a symbol secdef search.

    The constituent lane resolves an equity conid up front (a verified pin, or
    :meth:`CpRestDiscovery.underlying_conid`), so it does not need to *re-resolve* the conid the
    way :func:`resolve_index` does — it only needs the listed option months to drive discovery.

    The gateway **requires** ``symbol`` on ``/secdef/search`` (a conid-only call returns
    ``{"error": "symbol required"}``, which parses to no months → a spurious per-name no-capture).
    So the search is keyed by ``symbol``: the response carries one row per listing for that ticker
    (e.g. ASML lists on AEB/Eurex *and* NASDAQ), and :func:`parse_option_months_by_conid` reads the
    ``OPT`` months from the row that **is** the pinned/resolved European ``conid`` — so a globally
    ambiguous ticker still reads the Eurex months, not the US ones. An underlying with no option
    section yields ``()`` (a clean per-name no-capture, never a crash). The conid is *not*
    re-derived here; the caller already holds the verified one and uses it only to disambiguate.
    """
    results = transport.get("/iserver/secdef/search", params={"symbol": symbol})
    return parse_option_months_by_conid(results, conid=conid)
