"""A thin, request-frugal single-name Eurex option *entitlement* probe (T-§7.4 pre-flight).

The widened close capture (:mod:`.cp_rest_constituent_capture`) answers two questions in one
expensive run: (1) *does this account return tradeable Eurex option quotes for this name?* — a
boolean — and (2) *capture the full tradeable basket for analytics*. Job (1) is the real unknown on
a paper/trial account, and paying the full discovery+capture cost (hundreds of throttled
``/secdef/info`` + snapshot calls per name, across every listed month and the full strike ladder)
to answer a yes/no is wasteful and — given the CP Gateway's hard 10 req/s ceiling and its 10-minute
penalty box for offenders — actively dangerous when swept across ~50 constituents.

This module is the cheap probe. Per constituent it spends a **handful** of calls, not hundreds:

1. resolve the equity conid — a verified ``constituent_conids`` pin (no call) or one ``STK``
   ``/secdef/search`` (mirrors :func:`.cp_rest_constituent_capture` so the verdict matches the real
   lane's resolution);
2. fetch the listed option months via one symbol ``/secdef/search``
   (:func:`.cp_rest_index.option_months_for_conid`) — disambiguated by the resolved conid so a
   globally-ambiguous ticker reads its Eurex months, not a US homonym's;
3. snapshot the equity spot — one warm-up-polled ``/iserver/marketdata/snapshot``
   (:func:`.cp_rest_snapshot.snapshot_index_spot`) — to centre the strike pick;
4. read the **nearest** expiry's strikes — one ``/secdef/strikes`` — and pick the **one** strike
   nearest spot;
5. resolve that strike's call and put conids — two ``/secdef/info`` calls;
6. snapshot those two contracts — one warm-up-polled snapshot — and classify the quotes.

That is ~7 calls for an unpinned name (~6 pinned), independent of how many months/strikes the name
lists — a tiny fraction of the full lane's cost.

**The verdict vocabulary** mirrors the close-capture ledger's entitlement words and adds the
two-sided quote distinction the probe exists to make:

* ``unresolved`` — the equity conid would not resolve (the name is not a listing IBKR carries here);
* ``no_options`` — the name resolved but lists no option months;
* ``unentitled`` — the gateway refused the data with a 401/403 (the account is not entitled — the
  exact signal we are probing for on a paper/trial account);
* ``no_quote`` — both the near-ATM call and put came back with neither a bid nor an ask (subscribed
  but dark — no live market on the probed contract);
* ``one_sided`` — at least one of the two carried exactly one side of the market (a bid xor an ask);
* ``two_sided`` — at least one of the two carried BOTH a bid and an ask: a genuine tradeable Eurex
  option quote. *This* is the "yes" the probe is built to find.

**READ-ONLY.** The probe never writes to the canonical store. It reads membership (to resolve the
top-N) and hits the gateway's read endpoints only; it persists nothing. The transport is injected,
so the gate drives the whole probe against a fake gateway with no network and no secrets.
"""

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

# 401/403 are IBKR's authorization refusals for an unentitled instrument — the exact signal the
# probe exists to surface. Mirrors ``cp_rest_constituent_capture._UNENTITLED_STATUS`` verbatim so a
# name the probe calls ``unentitled`` is the same name the real lane would.
_UNENTITLED_STATUS = frozenset({401, 403})

# The full verdict vocabulary, ordered worst→best for the table summary. ``unresolved`` /
# ``no_options`` / ``unentitled`` mirror the close-capture ledger; the quote tiers are the probe's
# own contribution (the boolean the expensive lane never cheaply answered).
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
    """One constituent's entitlement verdict — the per-name row of the probe.

    ``outcome`` is one of :data:`PROBE_OUTCOMES`. ``rank``/``weight`` carry the name's place in the
    point-in-time top-N basket. ``conid`` is the resolved equity conid (``None`` when unresolved);
    ``expiry``/``strike`` are the near-ATM contract probed (``None`` before a contract was reached).
    ``detail`` is the one-line human reason. The verdict is purely a read of the gateway's
    responses — nothing here is persisted.
    """

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
    """The aggregate of probing an index's top-N constituents — the structured probe return.

    ``index`` is the probed index symbol, ``per_name`` the per-constituent verdicts in rank order,
    and ``counts`` the ``{outcome: n}`` tally over them (every :data:`PROBE_OUTCOMES` key present,
    zero-filled). ``entitled`` is the names that returned a genuine two-sided Eurex option quote —
    the set the full-depth capture should then target.
    """

    index: str
    per_name: tuple[ProbeOutcome, ...]
    counts: dict[str, int]

    @property
    def entitled(self) -> tuple[str, ...]:
        """The constituents that returned a two-sided quote (the names worth full capture)."""
        return tuple(row.constituent for row in self.per_name if row.outcome == "two_sided")


def _resolve_conid(
    discovery: CpRestDiscovery,
    *,
    constituent: str,
    pins: dict[str, int],
) -> int | None:
    """Resolve a constituent's equity conid — a verified pin (no call) or one ``STK`` search.

    Mirrors :func:`.cp_rest_constituent_capture._constituent_targets`: a ``constituent_conids`` pin
    wins over a bare-ticker search (the pin exists precisely for the names ``/secdef/search`` cannot
    disambiguate), and the remaining names resolve through :meth:`CpRestDiscovery.underlying_conid`,
    whose currency steers the venue. ``None`` when the search will not resolve (an ``unresolved``
    verdict) — never a raise, so one dead name does not abort the sweep.
    """
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
    """The one strike nearest ``spot`` (the near-ATM pick), or the median strike when spot is dark.

    The probe snapshots a single near-the-money contract, so it needs exactly one strike. With a
    spot it is the listed strike closest to it; with no spot (a dark equity snapshot) it falls back
    to the median listed strike — the same spot-less centring the chain planner uses — so the probe
    still picks a sensible ATM-ish contract rather than a deep wing. ``None`` only when the month
    lists no strikes at all.
    """
    if not strikes:
        return None
    ordered = sorted(strikes)
    if spot is None:
        return ordered[len(ordered) // 2]
    return min(ordered, key=lambda strike: (abs(strike - spot), strike))


def _classify_quotes(rows: Sequence[tuple[float | None, float | None]]) -> str:
    """Classify the near-ATM call+put bid/ask pairs into the quote-tier verdict.

    Independent of any capture predicate: a side is "present" when it is a non-``None`` value. A row
    is two-sided when BOTH sides are present, one-sided when exactly one is. The verdict is the best
    tier any of the probed contracts reached — ``two_sided`` if any is two-sided, else ``one_sided``
    if any has a single side, else ``no_quote`` (subscribed but dark). The call exists only over
    rows that were actually snapshotted (an unentitled/no-contract name never reaches here).
    """
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
    """Probe ONE constituent's Eurex option entitlement with a handful of read-only calls.

    The frugal chain (see the module docstring): resolve conid → option months → spot → nearest
    expiry's strikes → the one near-ATM strike's call+put conids → snapshot those two → classify.
    Each stage short-circuits to a labelled :class:`ProbeOutcome` rather than walking on:

    * conid will not resolve → ``unresolved`` (0–1 calls);
    * no option months → ``no_options``;
    * a 401/403 anywhere → ``unentitled`` (the entitlement signal);
    * the near-ATM call+put came back with no usable side → ``no_quote``;
    * exactly one side on either → ``one_sided``; both on either → ``two_sided``.

    The transport is injected; nothing is written. A non-entitlement transport error degrades to a
    ``no_quote`` verdict carrying the error text — the name is reachable but yielded no usable quote
    this probe — never an abort, so one bad name does not stop the sweep over the other ~49.
    """
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
        # The nearest expiry month is the first listed token (the search returns them in listed,
        # near→far order — the same order the chain-window policy brackets from).
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
    """Resolve the call and put conids for the one near-ATM (month, strike) — two ``/info`` calls.

    Returns the snapshot-ready integer conids for both rights that resolved (the broker scalar
    coerced to int). A right that lists no contract, or whose conid will not coerce, is simply
    absent — the probe needs at least one of the two to have a quote to classify.
    """
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
    """Snapshot the probed contracts and return their ``(bid, ask)`` pairs (one warm-up snapshot).

    Reuses the shared warm-up snapshot engine (cold-snapshot polling + URI-safe batching) so the
    probe survives the same cold-first-call quirk the capture does. Only rows for the requested
    conids are kept; each yields its parsed ``(bid, ask)`` for :func:`_classify_quotes`.
    """
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
    """Probe the option entitlement of an index's point-in-time top-N constituents (read-only).

    Resolves the **point-in-time top-N constituents by weight** through the shared look-ahead-gated
    :func:`top_n_by_weight` (the same resolver the real capture lane uses — never a hand-set list,
    never today's membership for a past date; it *raises* on a missing-weight basket rather than
    rank a wrong top-N), then probes each with :func:`probe_constituent_entitlement` and aggregates
    the verdicts. An empty basket (no banked membership for the date) yields an empty result
    rather than a raise — the probe is a diagnostic, not the fire, so it reports "nothing to
    probe" cleanly (the script turns the empty result into its own labelled non-zero exit).

    ``as_of_date`` is the trade date to reconstruct membership as of (a ``datetime.date``); it is
    passed straight to the resolver. ``top_n`` is the selection size. The whole probe is read-only:
    it reads membership from ``store`` and hits the gateway's read endpoints, persisting nothing.
    """
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
    """Render a probe result as a fixed-width text table + a one-line outcome summary.

    A pure formatter (no I/O): one row per constituent in rank order with its weight, resolved
    conid, the near-ATM contract probed, the verdict, and the detail; then a tally line over
    :data:`PROBE_OUTCOMES` and the entitled-name list. The script prints this; tests can assert on
    it without capturing stdout.
    """
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
