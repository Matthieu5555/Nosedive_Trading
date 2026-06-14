"""Widen the EOD close capture to an index's top-N constituents' option chains (T-§7.4, S1).

The index lane (:mod:`.cp_rest_close_capture`) banks *one* index's option chain at close. The
flagship S1 dispersion book — and the implied-correlation signal layer (R3) — need per-name option
surfaces for the index's **top-N constituents by index weight**, point-in-time, registry-driven
(never a hand-set list, TARGET §0/§7.4). The analytics engine is already underlying-generic
(``IvPoint`` / ``SurfaceParameters`` / projection all key on ``underlying``), so this is a *capture
scope + universe resolution* lane: it adds no engine code, it just feeds more underlyings into the
same :class:`IndexBasket` the close stage already consumes.

This module:

* resolves the **point-in-time top-N constituents by weight** for the fired index
  (:func:`_top_n_by_weight` over 1A :func:`members` — the membership seam, see the WIRING note);
* resolves each constituent's equity conid the same way the OHLC backfill does — verified
  ``constituent_conids`` pins first (the names ``/secdef/search`` cannot disambiguate, e.g.
  Sanofi ``SAN``→``SAN1``), then a ``STK`` secdef search (:meth:`CpRestDiscovery.underlying_conid`)
  for the rest, the discovery's currency steering the venue (an EUR index resolves the European
  listing, never a US homonym);
* captures each constituent's option chain over the **same** grid/selection/close instant via the
  underlying-generic :func:`collect_target_basket`, and merges the index + constituent baskets into
  one :class:`IndexBasket` (instruments/events/masters concatenated, keyed by ``underlying``).

A constituent that lists no options, whose conid will not resolve, or whose own capture fails is
logged and **skipped** — one unresolved name never aborts the whole fire (the same non-fatal
discipline the OHLC backfill uses). The index leg itself is *not* optional: if the index capture
fails the fire fails, because that is the basket's spine.

No look-ahead. The top-N basket is resolved **as of the trade date** through the 1A point-in-time
resolver (never today's membership applied to a past date), and every captured event is stamped at
the index's own session close, exactly as the index lane. The transport is injected, so the gate
drives the whole widened capture against a fake gateway with no network and no secrets.

WIRING (membership top-N seam — :func:`_top_n_by_weight`):
    The point-in-time *top-N-by-weight* selector is owned by the parallel task
    ``infra-sx5e-weighted-membership`` as a pure resolver
    ``top_n_by_weight(store, index, as_of_date, n) -> tuple[BasketMember, ...]`` in
    ``algotrading.infra.universe``. That task is not yet merged into this branch, so this module
    carries a **minimal local stand-in** (:func:`_top_n_by_weight`) built on the already-landed
    :func:`members` + :func:`basket_weight_sum`: it ranks the as-of basket by weight (descending,
    deterministic name tie-break) and rejects a basket with any missing weight (you cannot rank
    what you do not know). On merge, delete :func:`_top_n_by_weight` and import the shared
    ``top_n_by_weight`` from ``algotrading.infra.universe`` — the call site signature is identical.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

import structlog
from algotrading.core.config import PlatformConfig
from algotrading.infra.actor import IndexBasket
from algotrading.infra.contracts import InstrumentKey, InstrumentMaster, RawMarketEvent
from algotrading.infra.storage import ParquetStore
from algotrading.infra.universe import (
    BasketMember,
    ChainSelection,
    IndexEntry,
    MembershipError,
    basket_weight_sum,
    members,
)

from ..connectivity.cp_rest_transport import SupportsRestGet
from .cp_rest_close_capture import (
    CaptureTarget,
    collect_live_basket,
    collect_target_basket,
)
from .cp_rest_discovery import CpRestDiscovery
from .cp_rest_index import option_months_for_conid

__all__ = ["collect_index_and_constituents_basket"]

_LOGGER = structlog.get_logger("ibkr.constituent_capture")

_EQUITY_SECURITY_TYPE = "STK"


def _top_n_by_weight(
    store: ParquetStore, index: str, as_of_date: date, n: int
) -> tuple[BasketMember, ...]:
    """STUB of the parallel ``infra-sx5e-weighted-membership`` ``top_n_by_weight`` resolver.

    Returns the ``n`` constituents with the largest index weight in the basket *as it stood on*
    ``as_of_date`` (1A :func:`members`, point-in-time — never today's membership applied to a past
    date). Ranks by weight descending with a deterministic constituent-name tie-break, so equal
    weights resolve to a stable set across runs. Rejects a basket that carries any missing weight
    with a labeled :class:`MembershipError`: you cannot rank what you do not know, and silently
    dropping the unweighted names would pick a *wrong* top-N (the economic-correctness trap the
    membership task calls out). ``n`` is taken from config by the caller and must be ``>= 1``.

    This is a minimal local stand-in until ``infra-sx5e-weighted-membership`` merges its shared
    pure resolver; the call-site signature matches it exactly, so the swap is a one-line import
    change (see the module WIRING note).
    """
    if n < 1:
        raise MembershipError(index, "n", n, "top-N count must be >= 1")
    basket = members(store, index, as_of_date)
    if not basket:
        return ()
    if basket_weight_sum(basket) is None:
        # Any None weight makes the whole basket unrankable — refuse, do not rank the partial set.
        missing = sorted(m.constituent for m in basket if m.weight is None)
        raise MembershipError(
            index,
            "weight",
            None,
            f"cannot resolve top-{n} by weight: {len(missing)} constituent(s) have no weight "
            f"({', '.join(missing[:5])}{'…' if len(missing) > 5 else ''}); "
            "ranking requires complete weights",
        )
    ranked = sorted(basket, key=lambda m: (-(m.weight or 0.0), m.constituent))
    return tuple(ranked[:n])


def _constituent_targets(
    transport: SupportsRestGet,
    *,
    index: IndexEntry,
    top_n: Sequence[BasketMember],
) -> list[CaptureTarget]:
    """Resolve each top-N constituent to a :class:`CaptureTarget` (pins first, then secdef search).

    Verified ``constituent_conids`` pins win over a bare-ticker search — they exist precisely for
    the names ``/secdef/search`` cannot disambiguate (a ticker two listings share, e.g. Euronext
    Paris ``SAN``=Sanofi vs Bolsa-de-Madrid ``SAN``=Santander; IBKR renames one ``SAN1``) — and a
    pin is fetched straight by its unique conid, no search. The remaining names resolve their equity
    conid through :meth:`CpRestDiscovery.underlying_conid`, whose currency (the index's) steers the
    venue. The pin's label / the membership constituent symbol is the underlying key the chain and
    the OHLC bars both store under, so a name's bars and surface share one ``underlying``.

    A name whose conid will not resolve (a ticker IBKR does not list under that symbol/venue, a
    delisted member) is logged and dropped — it simply gets no chain this run, never aborting the
    sweep. Each underlying appears at most once; a pin shadows a same-label membership name.
    """
    log = _LOGGER.bind(index=index.symbol)
    pins = {label: conid for label, conid in index.ibkr.constituent_conids}
    wanted = {member.constituent for member in top_n}
    targets: list[CaptureTarget] = []
    seen: set[str] = set()
    discovery = CpRestDiscovery(transport, exchange=index.ibkr.exchange, currency=index.currency)
    for member in top_n:
        label = member.constituent
        if label in seen:
            continue
        seen.add(label)
        pinned = pins.get(label)
        if pinned is not None:
            targets.append(
                CaptureTarget(
                    symbol=label,
                    exchange=index.ibkr.exchange,
                    currency=index.currency,
                    security_type=_EQUITY_SECURITY_TYPE,
                    search_symbol=label,
                    conid=pinned,
                )
            )
            continue
        try:
            conid = discovery.underlying_conid(label)
        except Exception as exc:  # noqa: BLE001 — one unresolved constituent is non-fatal
            log.info(
                "ibkr.constituent_capture.unresolved_conid",
                constituent=label,
                error=str(exc),
            )
            continue
        targets.append(
            CaptureTarget(
                symbol=label,
                exchange=index.ibkr.exchange,
                currency=index.currency,
                security_type=_EQUITY_SECURITY_TYPE,
                search_symbol=label,
                conid=conid,
            )
        )
    # Defend the invariant the loop maintains: never a duplicate underlying, never a name outside
    # the requested top-N (a pin for a non-member must not sneak a chain into the basket).
    assert {t.symbol for t in targets} <= wanted
    return targets


def _capture_constituent(
    transport: SupportsRestGet,
    *,
    target: CaptureTarget,
    as_of: datetime,
    next_open: datetime,
    config: PlatformConfig,
    selection: ChainSelection | None,
) -> IndexBasket | None:
    """Capture one constituent's option basket; a per-name failure is non-fatal (logged, skipped).

    The constituent's conid is already resolved on the ``target`` (a pin or a secdef search), so
    only its listed option months are fetched (:func:`option_months_for_conid`) before delegating
    to the underlying-generic :func:`collect_target_basket`. A name that lists no options returns
    ``None`` (a clean no-capture for that name); a transport/discovery error is logged and turned
    into ``None`` so one bad constituent never aborts the index fire.
    """
    log = _LOGGER.bind(constituent=target.symbol, as_of=as_of.isoformat())
    conid = target.conid
    if conid is None:  # defensive: targets always carry a resolved conid by construction
        log.info("ibkr.constituent_capture.no_conid", reason="target has no resolved conid")
        return None
    try:
        months = option_months_for_conid(transport, conid=conid)
        if not months:
            log.info("ibkr.constituent_capture.no_option_months", conid=conid)
            return None
        return collect_target_basket(
            transport,
            target=target,
            conid=conid,
            months=months,
            as_of=as_of,
            next_open=next_open,
            config=config,
            selection=selection,
        )
    except Exception as exc:  # noqa: BLE001 — one constituent's failure must not abort the fire
        log.info("ibkr.constituent_capture.capture_failed", conid=conid, error=str(exc))
        return None


def _merge_baskets(baskets: Sequence[IndexBasket]) -> IndexBasket:
    """Concatenate per-underlying baskets into one, keyed by ``underlying`` (no engine change).

    Instruments / events / masters are concatenated; ``run_analytics`` already partitions each by
    ``underlying``, so a single multi-underlying :class:`IndexBasket` is exactly the shape it
    consumes — the index and every captured constituent priced on the same grid at the same close.
    Order is the input order (index first, then constituents by capture order); event ids are
    content-addressed per the close-capture, so the merge introduces no order sensitivity.
    """
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
) -> IndexBasket | None:
    """Capture the index *and* its top-N-by-weight constituents' option chains as one basket (§7.4).

    The widened EOD close capture: it banks the index's option chain (the spine — its failure
    fails the fire) and, on the same grid / selection / close instant, the option chains of the
    index's **point-in-time top-N constituents by index weight**
    (``config.universe.constituent_top_n``, resolved through the 1A membership weights — never a
    hand-set list, never today's membership for a past date). All legs are merged into one
    :class:`IndexBasket`, keyed by ``underlying``, which the underlying-generic ``run_analytics``
    prices unchanged.

    Returns ``None`` only when the index itself lists no qualifiable option chain (the index-only
    lane's clean no-capture). A constituent that lists no options, whose conid will not resolve, or
    whose capture errors is logged and skipped — one bad name never aborts the fire. The top-N
    resolution rejects a basket with any missing weight (you cannot rank what you do not know); a
    weighted source must be banked first (``scripts/ingest_membership.py``).

    ``as_of`` is the index's own session close (every captured event is stamped there);
    ``next_open`` bounds the admitted close set to ``[as_of, next_open)`` (the look-ahead guard).
    ``store`` reads the as-of membership; the transport is injected so the capture runs on a fake.
    """
    log = _LOGGER.bind(index=index.symbol, as_of=as_of.isoformat())
    index_basket = collect_live_basket(
        transport,
        index=index,
        as_of=as_of,
        next_open=next_open,
        config=config,
        selection=selection,
    )
    if index_basket is None:
        log.info(
            "ibkr.constituent_capture.index_no_options",
            reason="index lists no qualifiable options — no-capture day, constituents not swept",
        )
        return None

    top_n_count = config.universe.constituent_top_n
    top_n = _top_n_by_weight(store, index.symbol, as_of.date(), top_n_count)
    if not top_n:
        log.info(
            "ibkr.constituent_capture.no_constituents",
            reason="no banked membership for the index as of the trade date — index leg only",
            as_of_date=as_of.date().isoformat(),
        )
        return index_basket

    targets = _constituent_targets(transport, index=index, top_n=top_n)
    baskets: list[IndexBasket] = [index_basket]
    captured_names: list[str] = []
    for target in targets:
        constituent_basket = _capture_constituent(
            transport,
            target=target,
            as_of=as_of,
            next_open=next_open,
            config=config,
            selection=selection,
        )
        if constituent_basket is not None:
            baskets.append(constituent_basket)
            captured_names.append(target.symbol)

    log.info(
        "ibkr.constituent_capture.captured",
        top_n_requested=top_n_count,
        top_n_resolved=len(top_n),
        constituents_captured=len(captured_names),
        names=captured_names,
    )
    return _merge_baskets(baskets)
