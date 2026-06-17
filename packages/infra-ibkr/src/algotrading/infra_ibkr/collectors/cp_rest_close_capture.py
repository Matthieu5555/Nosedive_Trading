from __future__ import annotations

from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import structlog
from algotrading.core.config import PlatformConfig, StrikeSelectionConfig
from algotrading.infra.actor import IndexBasket
from algotrading.infra.contracts import InstrumentKey, InstrumentMaster, RawMarketEvent
from algotrading.infra.snapshots import assess_quote
from algotrading.infra.universe import (
    AvailableChain,
    ChainSelection,
    IndexEntry,
    OptionContract,
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
from .cp_rest_discovery_cache import (
    CachedChain,
    DiscoveryCache,
    revalidate_conids,
)
from .cp_rest_index import resolve_index
from .cp_rest_normalize import snapshot_to_events
from .cp_rest_snapshot import WarmupConfig, snapshot_index_spot, snapshot_with_warmup
from .cp_rest_wire import SnapshotRow, coerce_int_or_none
from .market_fields import to_datetime

__all__ = [
    "CaptureTarget",
    "CloseCaptureError",
    "DiscoveryRunawayError",
    "collect_live_basket",
    "collect_target_basket",
    "target_from_index",
]

_LOGGER = structlog.get_logger("ibkr.close_capture")

_INDEX_SECURITY_TYPE = "IND"
_EQUITY_SECURITY_TYPE = "STK"
_UNDERLYING_MULTIPLIER = 1.0
_OPTION_SECURITY_TYPE = "OPT"


@dataclass(frozen=True, slots=True)
class CaptureTarget:

    symbol: str
    exchange: str
    currency: str
    security_type: str = _EQUITY_SECURITY_TYPE
    search_symbol: str | None = None
    conid: int | None = None

    @property
    def resolved_search_symbol(self) -> str:
        return self.search_symbol or self.symbol


def target_from_index(index: IndexEntry) -> CaptureTarget:
    return CaptureTarget(
        symbol=index.symbol,
        exchange=index.ibkr.exchange,
        currency=index.currency,
        security_type=_INDEX_SECURITY_TYPE,
        search_symbol=index.ibkr_search_symbol,
        conid=None,
    )


def _underlying_key(target: CaptureTarget, conid: int) -> InstrumentKey:
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
) -> tuple[AvailableChain, dict[str, str], dict[str, str]]:
    log = _LOGGER.bind(underlying=target.symbol, as_of=as_of.isoformat())
    work_items: list[tuple[str, float, str]] = []
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
                work_items.append((month, strike, right))

    pool_size = min(max(strike_selection.discovery_pool_size, 1), max(len(work_items), 1))
    contracts = _qualify_contracts_concurrently(
        discovery,
        target=target,
        conid=conid,
        work_items=work_items,
        pool_size=pool_size,
        log=log,
    )

    expirations: set[str] = set()
    strikes: set[float] = set()
    conid_by_contract: dict[str, str] = {}
    month_by_token: dict[str, str] = {}
    multiplier = "100"
    for month, token, contract in sorted(contracts, key=lambda triple: triple[1]):
        multiplier = str(contract.multiplier)
        expirations.add(contract.expiry.strftime("%Y%m%d"))
        strikes.add(float(contract.strike))
        conid_by_contract[token] = str(contract.broker_contract_id)
        month_by_token[token] = month
    chain = AvailableChain(
        exchange=target.exchange,
        trading_class=target.symbol,
        multiplier=multiplier,
        expirations=tuple(sorted(expirations)),
        strikes=tuple(sorted(strikes)),
    )
    return chain, conid_by_contract, month_by_token


def _chain_from_cache(cached: CachedChain, target: CaptureTarget) -> AvailableChain:
    return AvailableChain(
        exchange=target.exchange,
        trading_class=target.symbol,
        multiplier=cached.multiplier or "100",
        expirations=tuple(cached.expirations),
        strikes=tuple(cached.strikes),
    )


def _qualify_contracts_concurrently(
    discovery: CpRestDiscovery,
    *,
    target: CaptureTarget,
    conid: int,
    work_items: Sequence[tuple[str, float, str]],
    pool_size: int,
    log: Any,
) -> list[tuple[str, str, OptionContract]]:

    def qualify(item: tuple[str, float, str]) -> list[tuple[str, str, OptionContract]]:
        month, strike, right = item
        resolved: list[tuple[str, str, OptionContract]] = []
        for contract in discovery.contracts(
            conid,
            symbol=target.resolved_search_symbol,
            month=month,
            strike=strike,
            right=right,
        ):
            if contract.broker_contract_id is None:
                continue
            token = _contract_token(contract.expiry, float(contract.strike), right)
            resolved.append((month, token, contract))
        return resolved

    log.info(
        "ibkr.close_capture.discovery_pool",
        underlying=target.symbol,
        work_items=len(work_items),
        pool_size=pool_size,
    )
    contracts: list[tuple[str, str, OptionContract]] = []
    with ThreadPoolExecutor(max_workers=pool_size) as pool:
        for resolved in pool.map(qualify, work_items):
            contracts.extend(resolved)
    return contracts


def _contract_token(expiry: date, strike: float, right: str) -> str:
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


@dataclass(frozen=True, slots=True)
class _PromotedSnapshots:

    events: tuple[RawMarketEvent, ...]
    option_row_count: int
    two_sided_count: int
    drop_reasons: tuple[tuple[str, str], ...]


def _two_sided_quote_reason(row: SnapshotRow, *, max_spread_pct: float) -> str | None:
    bid = row.bid
    ask = row.ask
    if bid is None or ask is None:
        return "missing_side"
    if ask <= 0.0:
        return "non_positive_ask"
    assessment = assess_quote(bid=bid, ask=ask, max_spread_pct=max_spread_pct)
    if "crossed" in assessment.reasons:
        return "crossed"
    if "non_positive_bid" in assessment.reasons:
        return "non_positive_bid"
    return None


def _snapshot_events(
    transport: SupportsRestGet,
    *,
    keys_by_conid: Mapping[int, InstrumentKey],
    underlying: str,
    session_id: str,
    as_of: datetime,
    next_open: datetime,
    max_spread_pct: float,
    warmup: WarmupConfig | None = None,
) -> _PromotedSnapshots:
    if not keys_by_conid:
        return _PromotedSnapshots(events=(), option_row_count=0, two_sided_count=0, drop_reasons=())
    rows = snapshot_with_warmup(transport, conids=sorted(keys_by_conid), warmup=warmup)
    next_open_ms = int(next_open.timestamp() * 1000)
    kept: list[tuple[InstrumentKey, SnapshotRow]] = []
    for row in rows:
        if row.conid is None:
            continue
        instrument = keys_by_conid.get(row.conid)
        if instrument is None:
            continue
        if row.updated_ms is not None and row.updated_ms >= next_open_ms:
            _LOGGER.info(
                "ibkr.close_capture.drop_later_session",
                conid=row.conid,
                updated_ms=row.updated_ms,
                next_open_ms=next_open_ms,
            )
            continue
        kept.append((instrument, row))
    option_row_count = 0
    two_sided_count = 0
    drop_reasons: list[tuple[str, str]] = []
    two_sided: list[tuple[InstrumentKey, SnapshotRow]] = []
    quarantined: list[tuple[InstrumentKey, SnapshotRow]] = []
    for instrument, row in kept:
        if not instrument.is_option():
            two_sided.append((instrument, row))
            continue
        option_row_count += 1
        reason = _two_sided_quote_reason(row, max_spread_pct=max_spread_pct)
        if reason is None:
            two_sided_count += 1
            two_sided.append((instrument, row))
            continue
        drop_reasons.append((instrument.canonical(), reason))
        quarantined.append((instrument, row))
        _LOGGER.info(
            "ibkr.close_capture.quarantine_row",
            instrument_key=instrument.canonical(),
            reason=reason,
            bid=row.bid,
            ask=row.ask,
            last=row.last,
        )
    two_sided.sort(key=lambda pair: pair[0].canonical())
    quarantined.sort(key=lambda pair: pair[0].canonical())
    events: list[RawMarketEvent] = []
    for instrument, row in [*two_sided, *quarantined]:
        # Stable identity (T-restore-overwrite-last-wins, blueprint 01-arch:17 idempotency): the close
        # is a POLL — one observation per (instrument, field) per day — so the event_id must NOT depend
        # on the membership-sorted position (which drifts between re-fires as quotes pass/fail the
        # two-sided filter). ``sequence=0`` is constant; ``instrument_key`` + ``field_name`` already
        # identify the observation, so a re-poll of the same close dedups to one row (idempotent).
        # Keep the three timestamps distinct (blueprint 01-architecture §60): exchange_ts is the
        # broker's real update time (the row's ``_updated``, ms→datetime), preserved rather than
        # discarded; receipt_ts and canonical_ts stay at the session close ``as_of`` — the
        # normalized ordering/as-of clock all close marks share (so the snapshot builder still
        # treats a post-close settlement mark as the close, and the derived analytics are intact).
        row_exchange_ts = (
            to_datetime(row.updated_ms * 1_000_000) if row.updated_ms is not None else as_of
        )
        events.extend(
            snapshot_to_events(
                row,
                instrument_key=instrument.canonical(),
                underlying=underlying,
                session_id=session_id,
                sequence=0,
                exchange_ts=row_exchange_ts,
                receipt_ts=as_of,
                canonical_ts=as_of,
            )
        )
    return _PromotedSnapshots(
        events=tuple(events),
        option_row_count=option_row_count,
        two_sided_count=two_sided_count,
        drop_reasons=tuple(drop_reasons),
    )


def _resolve_chain(
    transport: SupportsRestGet,
    discovery: CpRestDiscovery,
    *,
    target: CaptureTarget,
    conid: int,
    months: Sequence[str],
    selection: ChainSelection,
    spot: float | None,
    as_of: datetime,
    config: PlatformConfig,
    discovery_cache: DiscoveryCache | None,
    revalidate_cached_conids: bool,
    log: Any,
) -> tuple[AvailableChain, dict[str, str]]:
    if discovery_cache is not None:
        cached = discovery_cache.load(underlying=target.symbol, capture_date=as_of.date())
        if cached is not None:
            conid_by_contract = dict(cached.conid_by_contract)
            if revalidate_cached_conids:
                conid_by_contract = _revalidate_cached(
                    transport, conid_by_contract, log=log, underlying=target.symbol
                )
            if conid_by_contract:
                log.info(
                    "ibkr.close_capture.cache_hit",
                    underlying=target.symbol,
                    cache_as_of=cached.as_of_date.isoformat(),
                    contracts=len(conid_by_contract),
                    revalidated=revalidate_cached_conids,
                )
                return _chain_from_cache(cached, target), conid_by_contract
            log.info(
                "ibkr.close_capture.cache_empty_after_revalidate",
                underlying=target.symbol,
                reason="every cached conid delisted — falling back to live discovery",
            )

    chain, conid_by_contract, month_by_token = _discover_chain(
        discovery,
        target=target,
        conid=conid,
        months=months,
        selection=selection,
        spot=spot,
        as_of=as_of.date(),
        strike_selection=config.universe.strike_selection,
    )
    if discovery_cache is not None and conid_by_contract:
        discovery_cache.store_chain(
            underlying=target.symbol,
            as_of=as_of.date(),
            exchange=target.exchange,
            multiplier=chain.multiplier,
            months=tuple(months),
            expirations=chain.expirations,
            strikes=chain.strikes,
            conid_by_contract=conid_by_contract,
            entry_month_by_token=month_by_token,
        )
    return chain, conid_by_contract


def _revalidate_cached(
    transport: SupportsRestGet,
    conid_by_contract: dict[str, str],
    *,
    log: Any,
    underlying: str,
) -> dict[str, str]:
    candidate_conids = [
        coerced
        for raw in conid_by_contract.values()
        if (coerced := coerce_int_or_none(raw)) is not None
    ]
    valid = revalidate_conids(transport, candidate_conids)
    kept = {
        token: raw
        for token, raw in conid_by_contract.items()
        if (coerced := coerce_int_or_none(raw)) is not None and coerced in valid
    }
    dropped = len(conid_by_contract) - len(kept)
    if dropped:
        log.info(
            "ibkr.close_capture.revalidate_dropped",
            underlying=underlying,
            dropped=dropped,
            kept=len(kept),
        )
    return kept


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
    discovery_cache: DiscoveryCache | None = None,
    revalidate_cached_conids: bool = False,
    warmup: WarmupConfig | None = None,
    option_exchange: str | None = None,
) -> IndexBasket | None:
    log = _LOGGER.bind(underlying=target.symbol, as_of=as_of.isoformat())
    selection = selection or _selection_from_config(config)
    # Strikes/contracts are venue-scoped: a constituent's options usually list on
    # its national derivatives exchange (MEFFRV, BELFOX, ...), not the index's
    # exchange. ``option_exchange`` carries the venue the secdef listing reported;
    # falling back to ``target.exchange`` preserves the index-capture path.
    discovery = CpRestDiscovery(
        transport, exchange=option_exchange or target.exchange, currency=target.currency
    )
    spot = snapshot_index_spot(transport, conid, warmup=warmup)
    chain, conid_by_contract = _resolve_chain(
        transport,
        discovery,
        target=target,
        conid=conid,
        months=months,
        selection=selection,
        spot=spot,
        as_of=as_of,
        config=config,
        discovery_cache=discovery_cache,
        revalidate_cached_conids=revalidate_cached_conids,
        log=log,
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
            log.info(
                "ibkr.close_capture.skip_unparseable_conid",
                instrument_key=key.canonical(),
                broker_contract_id=key.broker_contract_id,
            )
            continue
        keys_by_conid[option_conid] = key

    session_id = f"{target.symbol}:{as_of.date().isoformat()}"
    qc = config.qc_threshold
    promoted = _snapshot_events(
        transport,
        keys_by_conid=keys_by_conid,
        underlying=target.symbol,
        session_id=session_id,
        as_of=as_of,
        next_open=next_open,
        max_spread_pct=qc.max_spread_pct,
        warmup=warmup,
    )
    events = list(promoted.events)

    instruments = (underlying_key, *kept_options)
    masters = tuple(_master(key, as_of) for key in instruments)
    two_sided_fraction = (
        promoted.two_sided_count / promoted.option_row_count
        if promoted.option_row_count > 0
        else 0.0
    )
    log.info(
        "ibkr.close_capture.captured",
        conid=conid,
        option_count=len(kept_options),
        event_count=len(events),
        option_row_count=promoted.option_row_count,
        two_sided_count=promoted.two_sided_count,
        two_sided_fraction=two_sided_fraction,
        quarantined_count=len(promoted.drop_reasons),
        spot=spot,
    )
    if kept_options and not promoted.option_row_count:
        raise CloseCaptureError(
            f"{target.symbol}: snapshot returned {len(kept_options)} option contracts but kept 0 "
            f"rows after the look-ahead guard (as_of={as_of.isoformat()}, "
            f"next_open={next_open.isoformat()}) — empty close set, refusing to land it silently"
        )
    min_fraction = qc.quote_integrity.min_two_sided_fraction
    if promoted.option_row_count > 0 and two_sided_fraction < min_fraction:
        log.warning(
            "ibkr.close_capture.closed_market",
            reason="basket has no live two-sided quotes — last-only / market-closed capture; "
            "rows landed to raw faithfully, derived grid will be empty and QC will page",
            option_row_count=promoted.option_row_count,
            two_sided_count=promoted.two_sided_count,
            two_sided_fraction=two_sided_fraction,
            min_two_sided_fraction=min_fraction,
            quarantined=list(promoted.drop_reasons),
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
    discovery_cache: DiscoveryCache | None = None,
    revalidate_cached_conids: bool = False,
    warmup: WarmupConfig | None = None,
) -> IndexBasket | None:
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
        discovery_cache=discovery_cache,
        revalidate_cached_conids=revalidate_cached_conids,
        warmup=warmup,
    )


def _selection_from_config(config: PlatformConfig) -> ChainSelection:
    strike_selection = config.universe.strike_selection
    return ChainSelection(
        max_expiries=None,
        min_strikes_per_side=strike_selection.min_strikes_per_side,
        option_exchange=config.universe.exchange,
        strike_window_pct=strike_selection.strike_window_pct,
    )
