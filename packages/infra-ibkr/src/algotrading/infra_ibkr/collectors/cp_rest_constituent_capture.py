from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import structlog
from algotrading.core.config import PlatformConfig
from algotrading.infra.actor import IndexBasket
from algotrading.infra.contracts import (
    ConstituentCaptureOutcome,
    InstrumentKey,
    InstrumentMaster,
    RawMarketEvent,
)
from algotrading.infra.storage import ParquetStore
from algotrading.infra.universe import (
    BasketMember,
    ChainSelection,
    IndexEntry,
    top_n_by_weight,
)

from ..connectivity.cp_rest_transport import CpRestTransportError, SupportsRestGet
from .cp_rest_close_capture import (
    CaptureTarget,
    collect_live_basket,
    collect_target_basket,
)
from .cp_rest_discovery import CpRestDiscovery
from .cp_rest_discovery_cache import DiscoveryCache
from .cp_rest_index import option_months_for_conid
from .cp_rest_snapshot import WarmupConfig

__all__ = ["ConstituentLaneError", "collect_index_and_constituents_basket"]

_LOGGER = structlog.get_logger("ibkr.constituent_capture")

_EQUITY_SECURITY_TYPE = "STK"

_UNENTITLED_STATUS = frozenset({401, 403})

_OUTCOMES_TABLE = "constituent_capture_outcomes"


class ConstituentLaneError(Exception):

    def __init__(self, index: str, top_n: int, reason: str) -> None:
        self.index = index
        self.top_n = top_n
        self.reason = reason
        super().__init__(
            f"constituent capture lane for index {index!r} (top-{top_n}): {reason}"
        )


@dataclass(frozen=True, slots=True)
class _ConstituentResult:

    member: BasketMember
    rank: int
    outcome: str
    basket: IndexBasket | None
    n_options: int
    detail: str


def _constituent_targets(
    transport: SupportsRestGet,
    *,
    index: IndexEntry,
    top_n: Sequence[BasketMember],
) -> dict[str, CaptureTarget]:
    log = _LOGGER.bind(index=index.symbol)
    pins = {label: conid for label, conid in index.ibkr.constituent_conids}
    targets: dict[str, CaptureTarget] = {}
    discovery = CpRestDiscovery(transport, exchange=index.ibkr.exchange, currency=index.currency)
    for member in top_n:
        label = member.constituent
        if label in targets:
            continue
        pinned = pins.get(label)
        if pinned is not None:
            targets[label] = CaptureTarget(
                symbol=label,
                exchange=index.ibkr.exchange,
                currency=index.currency,
                security_type=_EQUITY_SECURITY_TYPE,
                search_symbol=label,
                conid=pinned,
            )
            continue
        try:
            conid = discovery.underlying_conid(label)
        except Exception as exc:  # noqa: BLE001 — one unresolved name is non-fatal (recorded below)
            log.info(
                "ibkr.constituent_capture.unresolved_conid",
                constituent=label,
                error=str(exc),
            )
            continue
        targets[label] = CaptureTarget(
            symbol=label,
            exchange=index.ibkr.exchange,
            currency=index.currency,
            security_type=_EQUITY_SECURITY_TYPE,
            search_symbol=label,
            conid=conid,
        )
    return targets


def _attempt_constituent(
    transport: SupportsRestGet,
    *,
    member: BasketMember,
    rank: int,
    target: CaptureTarget | None,
    as_of: datetime,
    next_open: datetime,
    config: PlatformConfig,
    selection: ChainSelection | None,
    discovery_cache: DiscoveryCache | None = None,
    revalidate_cached_conids: bool = False,
    warmup: WarmupConfig | None = None,
) -> _ConstituentResult:
    label = member.constituent
    log = _LOGGER.bind(constituent=label, as_of=as_of.isoformat())
    if target is None or target.conid is None:
        log.info("ibkr.constituent_capture.unresolved", constituent=label)
        return _ConstituentResult(
            member=member,
            rank=rank,
            outcome="unresolved",
            basket=None,
            n_options=0,
            detail=f"underlying conid did not resolve for {label!r}",
        )
    conid = target.conid
    try:
        months = option_months_for_conid(
            transport, symbol=target.resolved_search_symbol, conid=conid
        )
        if not months:
            log.info("ibkr.constituent_capture.no_option_months", conid=conid)
            return _ConstituentResult(
                member=member,
                rank=rank,
                outcome="no_options",
                basket=None,
                n_options=0,
                detail=f"conid {conid} lists no option months",
            )
        basket = collect_target_basket(
            transport,
            target=target,
            conid=conid,
            months=months,
            as_of=as_of,
            next_open=next_open,
            config=config,
            selection=selection,
            discovery_cache=discovery_cache,
            revalidate_cached_conids=revalidate_cached_conids,
            warmup=warmup,
        )
    except CpRestTransportError as exc:
        if exc.status_code in _UNENTITLED_STATUS:
            log.info(
                "ibkr.constituent_capture.unentitled",
                conid=conid,
                status_code=exc.status_code,
            )
            return _ConstituentResult(
                member=member,
                rank=rank,
                outcome="unentitled",
                basket=None,
                n_options=0,
                detail=f"account not entitled (HTTP {exc.status_code}) for conid {conid}",
            )
        log.info("ibkr.constituent_capture.capture_failed", conid=conid, error=str(exc))
        return _ConstituentResult(
            member=member,
            rank=rank,
            outcome="no_options",
            basket=None,
            n_options=0,
            detail=f"capture error for conid {conid}: {exc}",
        )
    except Exception as exc:  # noqa: BLE001 — one constituent's failure must not abort the fire
        log.info("ibkr.constituent_capture.capture_failed", conid=conid, error=str(exc))
        return _ConstituentResult(
            member=member,
            rank=rank,
            outcome="no_options",
            basket=None,
            n_options=0,
            detail=f"capture error for conid {conid}: {exc}",
        )
    if basket is None or not basket.instruments:
        log.info("ibkr.constituent_capture.no_options", conid=conid)
        return _ConstituentResult(
            member=member,
            rank=rank,
            outcome="no_options",
            basket=None,
            n_options=0,
            detail=f"conid {conid} captured no qualifiable options",
        )
    n_options = sum(1 for key in basket.instruments if key.is_option())
    return _ConstituentResult(
        member=member,
        rank=rank,
        outcome="captured",
        basket=basket,
        n_options=n_options,
        detail=f"captured {n_options} option leg(s)",
    )


def _ledger_rows(
    results: Sequence[_ConstituentResult],
    *,
    index: str,
    run_id: str,
    run_ts: datetime,
) -> list[ConstituentCaptureOutcome]:
    return [
        ConstituentCaptureOutcome(
            run_id=run_id,
            run_ts=run_ts,
            index=index,
            underlying=result.member.constituent,
            outcome=result.outcome,
            rank=result.rank,
            weight=result.member.weight if result.member.weight is not None else 0.0,
            n_options=result.n_options,
            detail=result.detail,
        )
        for result in results
    ]


def _merge_baskets(baskets: Sequence[IndexBasket]) -> IndexBasket:
    instruments: list[InstrumentKey] = []
    events: list[RawMarketEvent] = []
    masters: list[InstrumentMaster] = []
    for basket in baskets:
        instruments.extend(basket.instruments)
        events.extend(basket.events)
        masters.extend(basket.masters)
    return IndexBasket(
        instruments=tuple(instruments), events=tuple(events), masters=tuple(masters)
    )


def collect_index_and_constituents_basket(
    transport: SupportsRestGet,
    *,
    store: ParquetStore,
    index: IndexEntry,
    as_of: datetime,
    next_open: datetime,
    config: PlatformConfig,
    selection: ChainSelection | None = None,
    run_id: str | None = None,
    discovery_cache: DiscoveryCache | None = None,
    revalidate_cached_conids: bool = False,
    warmup: WarmupConfig | None = None,
) -> IndexBasket | None:
    log = _LOGGER.bind(index=index.symbol, as_of=as_of.isoformat())
    index_basket = collect_live_basket(
        transport,
        index=index,
        as_of=as_of,
        next_open=next_open,
        config=config,
        selection=selection,
        discovery_cache=discovery_cache,
        revalidate_cached_conids=revalidate_cached_conids,
        warmup=warmup,
    )
    if index_basket is None:
        log.info(
            "ibkr.constituent_capture.index_no_options",
            reason="index lists no qualifiable options — no-capture day, constituents not swept",
        )
        return None

    top_n_count = config.universe.constituent_top_n
    resolved_run_id = run_id if run_id is not None else as_of.isoformat()

    top_n = top_n_by_weight(store, index.symbol, as_of.date(), top_n_count)
    if not top_n:
        log.critical(
            "ibkr.constituent_capture.no_membership",
            top_n_requested=top_n_count,
            as_of_date=as_of.date().isoformat(),
            reason="scope includes constituents but no banked 1A membership weights exist for the "
            "index as of the trade date — cannot resolve top-N; ingest a weighted membership "
            "source before the capture stage (scripts/ingest_membership.py)",
        )
        raise ConstituentLaneError(
            index.symbol,
            top_n_count,
            "no banked 1A membership weights for the trade date — top-N could not be resolved; "
            "ingest a weighted membership source before the capture stage",
        )

    targets = _constituent_targets(transport, index=index, top_n=top_n)
    results = [
        _attempt_constituent(
            transport,
            member=member,
            rank=rank,
            target=targets.get(member.constituent),
            as_of=as_of,
            next_open=next_open,
            config=config,
            selection=selection,
            discovery_cache=discovery_cache,
            revalidate_cached_conids=revalidate_cached_conids,
            warmup=warmup,
        )
        for rank, member in enumerate(top_n, start=1)
    ]

    ledger = _ledger_rows(results, index=index.symbol, run_id=resolved_run_id, run_ts=as_of)
    if ledger:
        store.write(_OUTCOMES_TABLE, ledger)

    captured = [result for result in results if result.basket is not None]
    counts: dict[str, int] = {}
    for result in results:
        counts[result.outcome] = counts.get(result.outcome, 0) + 1
    log.info(
        "ibkr.constituent_capture.outcomes",
        top_n_requested=top_n_count,
        top_n_resolved=len(top_n),
        constituents_attempted=len(results),
        constituents_captured=len(captured),
        outcomes=counts,
        names={result.member.constituent: result.outcome for result in results},
    )

    if not results:
        log.critical(
            "ibkr.constituent_capture.none_attempted",
            top_n_requested=top_n_count,
            top_n_resolved=len(top_n),
            reason="scope includes constituents and membership resolved, but not one constituent "
            "was attempted — the silent-empty failure mode; failing loud",
        )
        raise ConstituentLaneError(
            index.symbol,
            top_n_count,
            f"{len(top_n)} constituent(s) resolved but none were attempted",
        )

    return _merge_baskets(
        [index_basket, *(result.basket for result in captured if result.basket is not None)]
    )
