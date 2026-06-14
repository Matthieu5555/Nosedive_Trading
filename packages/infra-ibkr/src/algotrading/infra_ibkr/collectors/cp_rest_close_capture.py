"""``collect_live`` — the real EOD close basket source over CP REST (ADR 0024/0031, WS 1C).

The EOD spine exposes one transport-agnostic injection point — the ``BasketSource`` the runner
threads into ``default_stages_builder`` — and its production default returns ``None`` (the clean
no-capture day) until a live source lands. This module is that source. Given an authenticated,
OAuth-signed :class:`CpRestTransport` and a fired index, it captures the index's EOD close basket
and returns the populated :class:`IndexBasket` the downstream analytics / ``project_grid`` /
persist stages already consume.

This module is the *orchestration* only; its mechanics live in three focused seams:

* :mod:`.cp_rest_snapshot` — the snapshot engine (URI-safe conid batching + cold-snapshot
  warm-up), shared with the live adapter;
* :mod:`.cp_rest_chain_window` — the discovery-window policy (month-token bracketing + the
  delta-driven, tenor-aware T-delta-window strike qualification) and its failure modes;
* :mod:`.cp_rest_wire` — the typed CP wire shapes and the verbatim broker-scalar coercions.

The capture is the two-stage chain-selection policy the platform fixes once
(:mod:`algotrading.infra.universe.chain_planning`), driven over CP REST:

1. **Resolve the index conid** from the symbol (:func:`resolve_index_conid`) — the live path does
   not trust the registry's ``conid: 0`` placeholder.
2. **Snapshot the index spot** so the chain is centred on the true level (the request-shaping
   spot the discovery window keys off).
3. **Discover the option chain** (:class:`CpRestDiscovery`: search → strikes → info), build the
   broker-neutral :class:`AvailableChain`, and **plan** it with :func:`plan_chain` (the nearest
   maturities and the strike window — the broker-pacing-safe discovery bound).
4. **Cap to the capture budget** with :func:`select_capture_keys` (the per-session strike budget,
   nearest-the-money) so a full chain is not blindly streamed.
5. **Snapshot the selected contracts** at the close and normalize them to ``RawMarketEvent`` rows
   through the same :func:`snapshot_to_events` the live adapter uses.
6. **Assemble the :class:`IndexBasket`** (instruments + close events + masters) — exactly the
   shape :func:`run_analytics` consumes; the economic 30Δ delta-band selection and the grid
   projection then run *inside* the analytics over this captured set.

No look-ahead: the capture *is* the session close. Every emitted event is stamped at the index's
own ``FiredIndex.as_of`` (the resolver's ``session_close``). The close set is the half-open
interval ``[as_of, next_open)``: a snapshot row whose broker update time lands in it (the
settlement-window marks the timer fires into, minutes after the close) is kept, and one stamped
at/after the *next session's open* (``FiredIndex.next_open``) is dropped — that is a later
session, i.e. a wrong-day catch-up snapshot, never folded into this close basket. Bounding on the
next open rather than the close instant itself is deliberate: the broker's ``_updated`` keeps
advancing through the settlement window after the close, so a guard pinned at the close instant
would drop the very post-close snapshot the timer is designed to take. Pure given the transport's
responses; the only clock reads are the injected ``as_of`` / ``next_open``.

Transport stays on CP REST (the settled decision, ADR 0024/0031): no Nautilus ``TradingNode`` is
introduced for capture. The HTTP layer is the injected transport, so the gate drives the whole
capture against a fake gateway with no network and no secrets.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

import structlog
from algotrading.core.config import PlatformConfig, StrikeSelectionConfig
from algotrading.infra.actor import IndexBasket
from algotrading.infra.contracts import InstrumentKey, InstrumentMaster, RawMarketEvent
from algotrading.infra.surfaces import tenor_years as tenor_year_fraction
from algotrading.infra.universe import (
    AvailableChain,
    ChainSelection,
    IndexEntry,
    plan_chain,
    select_capture_keys,
)

from ..connectivity.cp_rest_transport import SupportsRestGet
from .cp_rest_chain_window import (
    CloseCaptureError,
    DiscoveryRunawayError,
    qualify_strikes_for_expiry,
    select_discovery_months,
)
from .cp_rest_discovery import CpRestDiscovery
from .cp_rest_index import resolve_index
from .cp_rest_normalize import snapshot_to_events
from .cp_rest_snapshot import snapshot_index_spot, snapshot_with_warmup
from .cp_rest_wire import SnapshotRow, coerce_int_or_none

__all__ = [
    "CaptureTarget",
    "CloseCaptureError",
    "DiscoveryRunawayError",
    "collect_live_basket",
    "collect_target_basket",
    "target_from_index",
]

_LOGGER = structlog.get_logger("ibkr.close_capture")

# The index itself is a non-option underlying; its security type in our key space. The option
# multiplier IBKR lists is a string ("100"); the underlying leg carries a multiplier of 1.0 (it is
# not a contract with a lot size in our key space, only the options are). A constituent underlying
# is an equity ("STK") but is otherwise the same shape of non-option chain centre.
_INDEX_SECURITY_TYPE = "IND"
_EQUITY_SECURITY_TYPE = "STK"
_UNDERLYING_MULTIPLIER = 1.0
_OPTION_SECURITY_TYPE = "OPT"


@dataclass(frozen=True, slots=True)
class CaptureTarget:
    """One underlying to capture an option chain for — the index *or* a constituent (1C × 1I).

    The capture mechanics (resolve conid, snapshot spot, discover + plan + budget the chain,
    snapshot the close marks, assemble the basket) are identical for the index and for each
    constituent; they differ only in this small descriptor. Holding it in one frozen value keeps
    :func:`collect_target_basket` underlying-generic, so the constituent lane (1I) is a *scope*
    widening that reuses the index lane (1C) byte-for-byte rather than a parallel capture path.

    * ``symbol`` — the platform-wide key the basket, masters, and analytics store under (the
      index registry symbol, or the constituent's underlying key — the same one its OHLC bars
      land under, so a pinned constituent's bars and chain share an underlying).
    * ``search_symbol`` — the symbol the IBKR ``secdef`` door is searched by, when it differs
      from ``symbol`` (e.g. SX5E→ESTX50, SAN→SAN1); ``None`` means same as ``symbol``.
    * ``exchange`` — the IBKR routing/listing exchange the discovery and option keys carry.
    * ``currency`` — the contract currency (steers the constituent's venue preference on search).
    * ``security_type`` — the underlying leg's security type in our key space (``IND`` for the
      index, ``STK`` for an equity constituent); the option legs are always ``OPT``.
    * ``conid`` — the underlying's verified IBKR conid when already known (a pinned constituent,
      or an index whose conid was resolved upstream); ``None`` means resolve it at capture time
      from ``search_symbol`` (the index path, which never trusts the registry ``conid: 0``).
    """

    symbol: str
    exchange: str
    currency: str
    security_type: str = _EQUITY_SECURITY_TYPE
    search_symbol: str | None = None
    conid: int | None = None

    @property
    def resolved_search_symbol(self) -> str:
        """The symbol to resolve against IBKR — ``search_symbol`` override, else ``symbol``."""
        return self.search_symbol or self.symbol


def target_from_index(index: IndexEntry) -> CaptureTarget:
    """Build the :class:`CaptureTarget` for an index entry (the 1C index lane, unchanged).

    The index conid is left to runtime resolution (``conid=None``) so the live path never trusts
    the registry's ``conid: 0`` placeholder — exactly the prior behaviour, now expressed through
    the generic descriptor.
    """
    return CaptureTarget(
        symbol=index.symbol,
        exchange=index.ibkr.exchange,
        currency=index.currency,
        security_type=_INDEX_SECURITY_TYPE,
        search_symbol=index.ibkr_search_symbol,
        conid=None,
    )


def _underlying_key(target: CaptureTarget, conid: int) -> InstrumentKey:
    """The underlying's canonical :class:`InstrumentKey` (the chain's centre)."""
    return InstrumentKey(
        underlying_symbol=target.symbol,
        security_type=target.security_type,
        exchange=target.exchange,
        currency=target.currency,
        multiplier=_UNDERLYING_MULTIPLIER,
        broker_contract_id=str(conid),
    )


def _option_key(
    target: CaptureTarget, *, expiry: date, strike: float, right: str, multiplier: float, conid: str
) -> InstrumentKey:
    """One option contract's canonical :class:`InstrumentKey`, carrying its IBKR conid."""
    return InstrumentKey(
        underlying_symbol=target.symbol,
        security_type=_OPTION_SECURITY_TYPE,
        exchange=target.exchange,
        currency=target.currency,
        multiplier=multiplier,
        broker_contract_id=conid,
        expiry=expiry,
        strike=strike,
        option_right=right,
    )


def _master(instrument: InstrumentKey, as_of: datetime) -> InstrumentMaster:
    """The point-in-time master row for one instrument as known at the close."""
    return InstrumentMaster(
        instrument_key=instrument.canonical(),
        as_of_date=as_of.date(),
        instrument=instrument,
        raw_broker_payload="{}",
    )


def _discover_chain(
    discovery: CpRestDiscovery,
    *,
    target: CaptureTarget,
    conid: int,
    months: Sequence[str],
    selection: ChainSelection,
    spot: float | None,
    as_of: date,
    strike_selection: StrikeSelectionConfig,
) -> tuple[AvailableChain, dict[str, str]]:
    """Discover the listed chain for the underlying and build the broker-neutral ``AvailableChain``.

    Drives the CP three-step ``strikes`` → ``info`` sequence (the ``search`` already resolved
    the conid and the listed ``months``). Returns the assembled chain menu *and* a
    ``(expiry,strike,right) -> conid`` map so the capture stage can snapshot exactly the
    selected contracts by their resolved conid.

    Per expiry the qualified strike window is **delta-driven and tenor-aware**
    (:func:`qualify_strikes_for_expiry` → ``select_discovery_strikes``): it contains the
    30Δ band at that tenor (the band's strike width grows with √T), so the downstream economic
    selection can reach the true 30Δ strikes — never the ~ATM±1% sliver a fixed strike count
    delivered. ``info`` costs one paced call per (strike, right); the window is full-30Δ (no cap)
    but bounded in practice by the listed strikes, with the runaway valve as the only backstop.
    """
    log = _LOGGER.bind(underlying=target.symbol, as_of=as_of.isoformat())
    expirations: list[str] = []
    strikes: set[float] = set()
    conid_by_contract: dict[str, str] = {}
    multiplier = "100"
    for month in select_discovery_months(months, selection):
        calls, puts = discovery.strikes(conid, month=month)
        listed = set(calls) | set(puts)
        qualified = qualify_strikes_for_expiry(
            listed,
            month=month,
            spot=spot,
            as_of=as_of,
            strike_selection=strike_selection,
            log=log,
        )
        for strike in qualified:
            for right in ("C", "P"):
                for contract in discovery.contracts(
                    conid,
                    symbol=target.resolved_search_symbol,
                    month=month,
                    strike=strike,
                    right=right,
                ):
                    if contract.broker_contract_id is None:
                        continue
                    multiplier = str(contract.multiplier)
                    expiry_token = contract.expiry.strftime("%Y%m%d")
                    if expiry_token not in expirations:
                        expirations.append(expiry_token)
                    strikes.add(float(contract.strike))
                    conid_by_contract[
                        _contract_token(contract.expiry, float(contract.strike), right)
                    ] = contract.broker_contract_id
    chain = AvailableChain(
        exchange=target.exchange,
        trading_class=target.symbol,
        multiplier=multiplier,
        expirations=tuple(sorted(set(expirations))),
        strikes=tuple(sorted(strikes)),
    )
    return chain, conid_by_contract


def _contract_token(expiry: date, strike: float, right: str) -> str:
    """A stable key into the conid map for one (expiry, strike, right)."""
    return f"{expiry.isoformat()}|{strike:.10g}|{right}"


def _planned_option_keys(
    target: CaptureTarget,
    *,
    plan_expiries: Sequence[str],
    plan_strikes: Sequence[float],
    plan_rights: Sequence[str],
    multiplier: float,
    conid_by_contract: Mapping[str, str],
) -> list[InstrumentKey]:
    """Expand the plan's expiries × strikes × rights into the resolved option keys.

    Only contracts that actually qualified (have a conid in the discovery map) become keys —
    the cartesian a plan asks for is a superset; the ones that did not list are dropped, exactly
    as a broker adapter drops contracts that fail to qualify.
    """
    keys: list[InstrumentKey] = []
    for expiry_token in plan_expiries:
        expiry = date(int(expiry_token[0:4]), int(expiry_token[4:6]), int(expiry_token[6:8]))
        for strike in plan_strikes:
            for right in plan_rights:
                conid = conid_by_contract.get(_contract_token(expiry, strike, right))
                if conid is None:
                    continue
                keys.append(
                    _option_key(
                        target,
                        expiry=expiry,
                        strike=strike,
                        right=right,
                        multiplier=multiplier,
                        conid=conid,
                    )
                )
    return keys


def _snapshot_events(
    transport: SupportsRestGet,
    *,
    keys_by_conid: Mapping[int, InstrumentKey],
    underlying: str,
    session_id: str,
    as_of: datetime,
    next_open: datetime,
) -> list[RawMarketEvent]:
    """Snapshot the selected contracts at the close and normalize to ``RawMarketEvent`` rows.

    Every event is stamped at ``as_of`` (the session close) — both the exchange and receipt time
    — so the basket is the close set, byte-identical on replay. A snapshot row whose own update
    time (``_updated``) is at or after ``next_open`` (the next session's open) is dropped: it
    belongs to a later session, a wrong-day catch-up snapshot the close capture never folds in
    (the look-ahead guard). The admitted window is the half-open ``[as_of, next_open)``, so the
    post-close settlement marks the timer fires into — whose ``_updated`` is after ``as_of`` but
    before the next open — are kept, not dropped. A row for an unrequested conid is ignored.

    ``sequence`` is assigned from the kept contracts' *stable identity* (their canonical instrument
    key), NOT from the broker's response row order: a re-fire / retry that returns the same
    contracts in a different order must yield identical content-addressed event ids, so the
    append-only store dedupes the re-capture instead of keeping a second copy. Broker-supplied
    ``conid`` / ``_updated`` scalars coerce through the wire model's validators, so an unexpected
    payload shape skips the row rather than raising a bare ``ValueError``.
    """
    if not keys_by_conid:
        return []
    # Warm-up polled like the spot snapshot: a cold first call returns metadata-only rows (no
    # marks), which would yield a basket of contracts with no quotes — IV/Greeks could not price.
    rows = snapshot_with_warmup(transport, conids=sorted(keys_by_conid))
    next_open_ms = int(next_open.timestamp() * 1000)
    # First pass: keep the admitted (instrument, row) pairs, dropping unrequested conids, malformed
    # payloads, and post-close prints. Sequence is NOT assigned here — row order is not trusted.
    kept: list[tuple[InstrumentKey, SnapshotRow]] = []
    for row in rows:
        if row.conid is None:
            continue
        instrument = keys_by_conid.get(row.conid)
        if instrument is None:
            continue
        if row.updated_ms is not None and row.updated_ms >= next_open_ms:
            # A row updated at/after the next session's open belongs to a later session (a
            # wrong-day catch-up snapshot) — never in this close basket. A row updated in the
            # settlement window after the close but before the next open is kept (it is the close).
            _LOGGER.info(
                "ibkr.close_capture.drop_later_session",
                conid=row.conid,
                updated_ms=row.updated_ms,
                next_open_ms=next_open_ms,
            )
            continue
        kept.append((instrument, row))
    # Second pass: assign sequence by the contract's stable canonical key (not arrival order), so a
    # shuffled re-fire reproduces the same event ids.
    kept.sort(key=lambda pair: pair[0].canonical())
    events: list[RawMarketEvent] = []
    for sequence, (instrument, row) in enumerate(kept):
        events.extend(
            snapshot_to_events(
                row,
                instrument_key=instrument.canonical(),
                underlying=underlying,
                session_id=session_id,
                sequence=sequence,
                exchange_ts=as_of,
                receipt_ts=as_of,
            )
        )
    return events


def collect_target_basket(
    transport: SupportsRestGet,
    *,
    target: CaptureTarget,
    conid: int,
    months: Sequence[str],
    as_of: datetime,
    next_open: datetime,
    config: PlatformConfig,
    selection: ChainSelection | None = None,
) -> IndexBasket | None:
    """Capture one underlying's EOD close option basket over CP REST (the underlying-generic body).

    This is the capture mechanics, factored out of the index lane so the constituent lane (1I)
    reuses it byte-for-byte: given an already-resolved underlying ``conid`` and its listed option
    ``months``, it snapshots the spot to centre the chain, discovers and plans the option chain,
    caps it to the capture budget, snapshots the selected contracts at the close, and returns the
    populated :class:`IndexBasket` (the underlying leg + its option legs). Returns ``None`` (a
    clean, labeled empty capture — never a raise) when the underlying lists no qualifiable option
    chain, so a name with no options degrades to a no-capture rather than failing.

    ``selection`` defaults to a :class:`ChainSelection` built from the universe config's strike-
    selection knobs (nearest maturities, the per-session strike budget); the economic 30Δ band
    runs downstream in :func:`run_analytics`. ``as_of`` is the session close — every captured
    event is stamped there; ``next_open`` bounds the admitted close set to the half-open
    ``[as_of, next_open)`` (a later-session row is dropped). A snapshot that returns option
    contracts but keeps none after that guard raises :class:`CloseCaptureError` (a loud failure),
    never a silently-empty basket.
    """
    log = _LOGGER.bind(underlying=target.symbol, as_of=as_of.isoformat())
    selection = selection or _selection_from_config(config, as_of.date())
    discovery = CpRestDiscovery(
        transport, exchange=target.exchange, currency=target.currency
    )
    spot = snapshot_index_spot(transport, conid)
    chain, conid_by_contract = _discover_chain(
        discovery,
        target=target,
        conid=conid,
        months=months,
        selection=selection,
        spot=spot,
        as_of=as_of.date(),
        strike_selection=config.universe.strike_selection,
    )
    if not conid_by_contract:
        log.info("ibkr.close_capture.no_options", reason="underlying lists no qualifiable options")
        return None

    plan = plan_chain(target.symbol, [chain], spot=spot, selection=selection)
    if plan is None:
        log.info("ibkr.close_capture.no_plan", reason="no listing selected for the underlying")
        return None

    multiplier = float(plan.multiplier) if plan.multiplier else 100.0
    underlying_key = _underlying_key(target, conid)
    option_keys = _planned_option_keys(
        target,
        plan_expiries=plan.expiries,
        plan_strikes=plan.strikes,
        plan_rights=plan.rights,
        multiplier=multiplier,
        conid_by_contract=conid_by_contract,
    )

    # Cap to the per-session capture budget (nearest-the-money), then snapshot exactly those.
    spots = {target.symbol: spot} if spot is not None else {}
    captured = set(
        select_capture_keys(
            [underlying_key, *option_keys],
            spots=spots,
            selection=selection,
            exchange=target.exchange,
        )
    )
    kept_options = [key for key in option_keys if key.canonical() in captured]
    keys_by_conid: dict[int, InstrumentKey] = {conid: underlying_key}
    for key in kept_options:
        option_conid = coerce_int_or_none(key.broker_contract_id)
        if option_conid is None:
            # A broker-supplied contract id that will not coerce to an int cannot be snapshotted by
            # conid; skip it with a structured log rather than aborting the whole capture.
            log.info(
                "ibkr.close_capture.skip_unparseable_conid",
                instrument_key=key.canonical(),
                broker_contract_id=key.broker_contract_id,
            )
            continue
        keys_by_conid[option_conid] = key

    session_id = f"{target.symbol}:{as_of.date().isoformat()}"
    events = _snapshot_events(
        transport,
        keys_by_conid=keys_by_conid,
        underlying=target.symbol,
        session_id=session_id,
        as_of=as_of,
        next_open=next_open,
    )

    instruments = (underlying_key, *kept_options)
    masters = tuple(_master(key, as_of) for key in instruments)
    log.info(
        "ibkr.close_capture.captured",
        conid=conid,
        option_count=len(kept_options),
        event_count=len(events),
        spot=spot,
    )
    if kept_options and not events:
        # Contracts came back but every row was dropped as a later session: a wrong-day / wrong-time
        # capture, not a clean optionless no-op (that returned None far above). Fail loud so the
        # runner exits non-zero and OnFailure= alerts, rather than silently landing an empty day.
        raise CloseCaptureError(
            f"{target.symbol}: snapshot returned {len(kept_options)} option contracts but kept 0 "
            f"events after the look-ahead guard (as_of={as_of.isoformat()}, "
            f"next_open={next_open.isoformat()}) — empty close set, refusing to land it silently"
        )
    return IndexBasket(
        instruments=instruments, events=tuple(events), masters=masters
    )


def collect_live_basket(
    transport: SupportsRestGet,
    *,
    index: IndexEntry,
    as_of: datetime,
    next_open: datetime,
    config: PlatformConfig,
    selection: ChainSelection | None = None,
) -> IndexBasket | None:
    """Capture one fired index's EOD close basket over CP REST (the live ``BasketSource`` body).

    Resolves the index conid from its symbol (never the registry's ``conid: 0`` placeholder) and
    its listed option months in one secdef search, then delegates to the underlying-generic
    :func:`collect_target_basket`. The behaviour is the index lane as it always was — this is now
    a thin wrapper that pins the index :class:`CaptureTarget` and shares the capture mechanics with
    the constituent lane. Return-value and look-ahead semantics are :func:`collect_target_basket`'s.
    """
    resolved = resolve_index(
        transport, symbol=index.ibkr_search_symbol, exchange=index.ibkr.exchange
    )
    return collect_target_basket(
        transport,
        target=target_from_index(index),
        conid=resolved.conid,
        months=resolved.option_months,
        as_of=as_of,
        next_open=next_open,
        config=config,
        selection=selection,
    )


def _selection_from_config(config: PlatformConfig, as_of: date) -> ChainSelection:
    """Build the capture :class:`ChainSelection` from the universe strike-selection config.

    The maturity budget and per-side floor are economic and come from the typed
    ``universe.yaml`` (never a ``.py`` literal). The pinned ``tenor_grid`` labels are resolved to
    their ACT/365 year fractions through ``surfaces.projection.tenor_years`` — the **single home**
    of the label→year map — and passed with ``as_of`` (the trade date) so expiry selection targets
    the term structure (:func:`select_expiries_bracketing`) instead of the nearest few weeklies.
    ``max_expiries`` keeps the grid length as the legacy fallback budget. The %-of-spot window and
    option exchange keep their request-shaping defaults (a discovery heuristic, not an economic
    parameter).
    """
    strike_selection = config.universe.strike_selection
    grid = config.universe.tenor_grid
    return ChainSelection(
        max_expiries=len(grid),
        min_strikes_per_side=strike_selection.min_strikes_per_side,
        option_exchange=config.universe.exchange,
        tenor_years=tuple(tenor_year_fraction(label) for label in grid),
        as_of=as_of,
    )
