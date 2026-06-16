from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import structlog
from algotrading.infra.storage import ParquetStore
from algotrading.infra.universe import BasketMember, IndexEntry, top_n_by_weight

from ..connectivity.cp_rest_transport import CpRestTransportError, SupportsRestGet
from .cp_rest_discovery import CpRestDiscovery
from .cp_rest_index import option_months_for_conid
from .cp_rest_snapshot import snapshot_index_spot, snapshot_with_warmup

__all__ = [
    "ProbeOutcome",
    "ProbeResult",
    "format_probe_table",
    "probe_constituent_entitlement",
    "probe_index_entitlement",
]

_LOGGER = structlog.get_logger("ibkr.entitlement_probe")

_EQUITY_SECURITY_TYPE = "STK"

_UNENTITLED_STATUS = frozenset({401, 403})

PROBE_OUTCOMES: tuple[str, ...] = (
    "unresolved",
    "no_options",
    "unentitled",
    "no_quote",
    "one_sided",
    "two_sided",
)


@dataclass(frozen=True, slots=True)
class ProbeOutcome:

    constituent: str
    rank: int
    weight: float | None
    outcome: str
    conid: int | None
    expiry: str | None
    strike: float | None
    detail: str


@dataclass(frozen=True, slots=True)
class ProbeResult:

    index: str
    per_name: tuple[ProbeOutcome, ...]
    counts: dict[str, int]

    @property
    def entitled(self) -> tuple[str, ...]:
        return tuple(row.constituent for row in self.per_name if row.outcome == "two_sided")


def _resolve_conid(
    discovery: CpRestDiscovery,
    *,
    constituent: str,
    pins: dict[str, int],
) -> int | None:
    pinned = pins.get(constituent)
    if pinned is not None:
        return pinned
    try:
        return discovery.underlying_conid(constituent)
    except Exception as exc:  # noqa: BLE001 — one unresolved name is a recorded verdict, not fatal
        _LOGGER.info(
            "ibkr.entitlement_probe.unresolved_conid", constituent=constituent, error=str(exc)
        )
        return None


def _nearest_strike(strikes: Sequence[float], spot: float | None) -> float | None:
    if not strikes:
        return None
    ordered = sorted(strikes)
    if spot is None:
        return ordered[len(ordered) // 2]
    return min(ordered, key=lambda strike: (abs(strike - spot), strike))


def _classify_quotes(rows: Sequence[tuple[float | None, float | None]]) -> str:
    any_two_sided = False
    any_one_sided = False
    for bid, ask in rows:
        present = (bid is not None) + (ask is not None)
        if present == 2:
            any_two_sided = True
        elif present == 1:
            any_one_sided = True
    if any_two_sided:
        return "two_sided"
    if any_one_sided:
        return "one_sided"
    return "no_quote"


def probe_constituent_entitlement(
    transport: SupportsRestGet,
    *,
    member: BasketMember,
    rank: int,
    index: IndexEntry,
    pins: dict[str, int],
) -> ProbeOutcome:
    label = member.constituent
    log = _LOGGER.bind(constituent=label, index=index.symbol)
    discovery = CpRestDiscovery(transport, exchange=index.ibkr.exchange, currency=index.currency)

    conid = _resolve_conid(discovery, constituent=label, pins=pins)
    if conid is None:
        return ProbeOutcome(
            constituent=label, rank=rank, weight=member.weight, outcome="unresolved",
            conid=None, expiry=None, strike=None,
            detail=f"equity conid did not resolve for {label!r}",
        )

    try:
        months = option_months_for_conid(transport, symbol=label, conid=conid)
        if not months:
            return ProbeOutcome(
                constituent=label, rank=rank, weight=member.weight, outcome="no_options",
                conid=conid, expiry=None, strike=None,
                detail=f"conid {conid} lists no option months",
            )
        month = months[0]
        spot = snapshot_index_spot(transport, conid)
        calls, puts = discovery.strikes(conid, month=month)
        strike = _nearest_strike(tuple(set(calls) | set(puts)), spot)
        if strike is None:
            return ProbeOutcome(
                constituent=label, rank=rank, weight=member.weight, outcome="no_options",
                conid=conid, expiry=month, strike=None,
                detail=f"conid {conid} lists month {month} with no strikes",
            )
        contract_conids = _probe_contract_conids(
            discovery, conid=conid, label=label, month=month, strike=strike
        )
        if not contract_conids:
            return ProbeOutcome(
                constituent=label, rank=rank, weight=member.weight, outcome="no_options",
                conid=conid, expiry=month, strike=strike,
                detail=f"no call/put contract resolved at {month} {strike:g}",
            )
        quotes = _snapshot_quotes(transport, contract_conids)
    except CpRestTransportError as exc:
        if exc.status_code in _UNENTITLED_STATUS:
            log.info("ibkr.entitlement_probe.unentitled", conid=conid, status_code=exc.status_code)
            return ProbeOutcome(
                constituent=label, rank=rank, weight=member.weight, outcome="unentitled",
                conid=conid, expiry=None, strike=None,
                detail=f"account not entitled (HTTP {exc.status_code}) for conid {conid}",
            )
        log.info("ibkr.entitlement_probe.transport_error", conid=conid, error=str(exc))
        return ProbeOutcome(
            constituent=label, rank=rank, weight=member.weight, outcome="no_quote",
            conid=conid, expiry=None, strike=None,
            detail=f"transport error probing conid {conid}: {exc}",
        )

    outcome = _classify_quotes(quotes)
    log.info(
        "ibkr.entitlement_probe.classified",
        conid=conid, month=month, strike=strike, outcome=outcome,
    )
    return ProbeOutcome(
        constituent=label, rank=rank, weight=member.weight, outcome=outcome,
        conid=conid, expiry=month, strike=strike,
        detail=f"near-ATM {month} {strike:g} call+put → {outcome}",
    )


def _probe_contract_conids(
    discovery: CpRestDiscovery, *, conid: int, label: str, month: str, strike: float
) -> list[int]:
    found: list[int] = []
    for right in ("C", "P"):
        for contract in discovery.contracts(
            conid, symbol=label, month=month, strike=strike, right=right
        ):
            if contract.broker_contract_id is None:
                continue
            try:
                found.append(int(str(contract.broker_contract_id).strip()))
            except ValueError:
                continue
    return found


def _snapshot_quotes(
    transport: SupportsRestGet, contract_conids: Sequence[int]
) -> list[tuple[float | None, float | None]]:
    requested = frozenset(contract_conids)
    rows = snapshot_with_warmup(transport, conids=tuple(requested))
    return [(row.bid, row.ask) for row in rows if row.conid is not None and row.conid in requested]


def probe_index_entitlement(
    transport: SupportsRestGet,
    *,
    store: ParquetStore,
    index: IndexEntry,
    as_of_date: object,
    top_n: int,
) -> ProbeResult:
    from datetime import date as _date

    if not isinstance(as_of_date, _date):
        raise TypeError(f"as_of_date must be a datetime.date, got {as_of_date!r}")
    members = top_n_by_weight(store, index.symbol, as_of_date, top_n)
    pins = {label: conid for label, conid in index.ibkr.constituent_conids}
    per_name = tuple(
        probe_constituent_entitlement(
            transport, member=member, rank=rank, index=index, pins=pins
        )
        for rank, member in enumerate(members, start=1)
    )
    counts = {outcome: 0 for outcome in PROBE_OUTCOMES}
    for row in per_name:
        counts[row.outcome] += 1
    _LOGGER.info(
        "ibkr.entitlement_probe.aggregate",
        index=index.symbol,
        as_of_date=as_of_date.isoformat(),
        top_n_requested=top_n,
        probed=len(per_name),
        counts=counts,
    )
    return ProbeResult(index=index.symbol, per_name=per_name, counts=counts)


def format_probe_table(result: ProbeResult) -> str:
    header = (
        f"{'#':>3}  {'NAME':<10} {'WEIGHT':>8}  {'CONID':>10}  "
        f"{'CONTRACT':<16} {'VERDICT':<11} DETAIL"
    )
    lines = [f"Entitlement probe — {result.index} (top-{len(result.per_name)})", header]
    for row in result.per_name:
        weight = f"{row.weight:.4g}" if row.weight is not None else "-"
        conid = str(row.conid) if row.conid is not None else "-"
        contract = (
            f"{row.expiry} {row.strike:g}" if row.expiry and row.strike is not None
            else (row.expiry or "-")
        )
        lines.append(
            f"{row.rank:>3}  {row.constituent:<10} {weight:>8}  {conid:>10}  "
            f"{contract:<16} {row.outcome:<11} {row.detail}"
        )
    tally = "  ".join(f"{outcome}={result.counts[outcome]}" for outcome in PROBE_OUTCOMES)
    lines.append(f"summary: {tally}")
    entitled = result.entitled
    lines.append(
        f"entitled (two-sided): {', '.join(entitled) if entitled else '(none)'}"
    )
    return "\n".join(lines)
