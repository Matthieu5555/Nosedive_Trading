"""Widen the EOD close capture to an index's top-N constituents' option chains (T-§7.4, S1).

The index lane (:mod:`.cp_rest_close_capture`) banks *one* index's option chain at close. The
flagship S1 dispersion book — and the implied-correlation signal layer (R3) — need per-name option
surfaces for the index's **top-N constituents by index weight**, point-in-time, registry-driven
(never a hand-set list, TARGET §0/§7.4). The analytics engine is already underlying-generic
(``IvPoint`` / ``SurfaceParameters`` / projection all key on ``underlying``), so this is a *capture
scope + universe resolution* lane: it adds no engine code, it just feeds more underlyings into the
same :class:`IndexBasket` the close stage already consumes.

This module:

* resolves the **point-in-time top-N constituents by weight** for the fired index through the
  shared :func:`algotrading.infra.universe.top_n_by_weight` resolver (the one look-ahead-gated
  membership ranker — never today's membership for a past date, and it *refuses* to rank a basket
  with any missing weight rather than pick a wrong top-N);
* resolves each constituent's equity conid the same way the OHLC backfill does — verified
  ``constituent_conids`` pins first (the names ``/secdef/search`` cannot disambiguate, e.g.
  Sanofi ``SAN``→``SAN1``), then a ``STK`` secdef search (:meth:`CpRestDiscovery.underlying_conid`)
  for the rest, the discovery's currency steering the venue (an EUR index resolves the European
  listing, never a US homonym);
* captures each constituent's option chain over the **same** grid/selection/close instant via the
  underlying-generic :func:`collect_target_basket`, and merges the index + constituent baskets into
  one :class:`IndexBasket` (instruments/events/masters concatenated, keyed by ``underlying``).

**Fail-loud, never silent (EMERGENCY-constituent-lane-activation).** The 2026-06-15 SX5E canary
bound scope ``index+constituents`` yet captured zero constituents *with no error* — the worst
failure mode. The lane now treats "constituents are in scope but none could even be attempted" as
a CRITICAL failure that *raises* (so the runner exits non-zero and ``OnFailure=`` alerts fire),
never a clean exit. The cases are kept distinct:

* **no banked membership at all** (the canary's exact gap — the resolver returns an empty basket
  because no 1A weights were ingested for the index as of the trade date) → a CRITICAL
  :class:`ConstituentLaneError`, naming the missing input;
* **membership present but unrankable** (any constituent has a labeled-unavailable weight) → the
  shared resolver already raises :class:`MembershipRankingError` (also loud), propagated unchanged;
* **names resolved but every one is unentitled / lists no options / will not resolve** → that is a
  *real, recorded* outcome, not a wiring bug: each name lands a labelled row in the per-name ledger.
  The lane raises only if *not a single* constituent could be attempted at all.

**Per-name outcome ledger.** Every constituent the lane attempts records exactly one labelled
:class:`ConstituentCaptureOutcome` — ``captured(n_options)`` / ``no_options`` / ``unentitled`` /
``unresolved`` — persisted to the ``constituent_capture_outcomes`` table (partitioned under
``underlying=<SYMBOL>``) so we finally learn *which* of the index's heaviest names return option
chains on this account. The capture-coverage panel surfaces it.

No look-ahead. The top-N basket is resolved **as of the trade date** through the 1A point-in-time
resolver (never today's membership applied to a past date), and every captured event is stamped at
the index's own session close, exactly as the index lane. The transport is injected, so the gate
drives the whole widened capture against a fake gateway with no network and no secrets.
"""

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
from .cp_rest_index import option_months_for_conid

__all__ = ["ConstituentLaneError", "collect_index_and_constituents_basket"]

_LOGGER = structlog.get_logger("ibkr.constituent_capture")

_EQUITY_SECURITY_TYPE = "STK"

# A transport status that means "the account is not entitled to this data" (vs a transient or a
# genuine not-found). 401/403 are the authorization refusals IBKR returns for an unentitled
# instrument; they are recorded as a per-name ``unentitled`` outcome, not a generic failure.
_UNENTITLED_STATUS = frozenset({401, 403})

# The ledger table the per-name outcomes land in (registered in the contracts table registry).
_OUTCOMES_TABLE = "constituent_capture_outcomes"


class ConstituentLaneError(Exception):
    """The constituent lane was in scope but could not attempt a single constituent (CRITICAL).

    Raised when scope includes constituents yet *zero* were resolved/attempted, so the silent
    empty-capture that hid the 2026-06-15 canary becomes a loud, non-zero-exit failure instead.
    Carries the ``index``, the constituent ``top_n`` that was requested, and a plain-language
    ``reason`` naming the missing input (e.g. no banked 1A membership weights for the trade date),
    so ``OnFailure=`` fires with a diagnosable message rather than a clean exit.
    """

    def __init__(self, index: str, top_n: int, reason: str) -> None:
        self.index = index
        self.top_n = top_n
        self.reason = reason
        super().__init__(
            f"constituent capture lane for index {index!r} (top-{top_n}): {reason}"
        )


@dataclass(frozen=True, slots=True)
class _ConstituentResult:
    """The outcome of attempting one constituent: its basket (if any) and its labelled verdict.

    ``basket`` is the captured :class:`IndexBasket` for a ``captured`` outcome and ``None`` for
    every other label. ``n_options`` is the captured option-leg count (``0`` for a non-capture).
    ``detail`` is the one-line human reason recorded in the ledger.
    """

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
    """Resolve each top-N constituent to a :class:`CaptureTarget` (pins first, then secdef search).

    Verified ``constituent_conids`` pins win over a bare-ticker search — they exist precisely for
    the names ``/secdef/search`` cannot disambiguate (a ticker two listings share, e.g. Euronext
    Paris ``SAN``=Sanofi vs Bolsa-de-Madrid ``SAN``=Santander; IBKR renames one ``SAN1``) — and a
    pin is fetched straight by its unique conid, no search. The remaining names resolve their equity
    conid through :meth:`CpRestDiscovery.underlying_conid`, whose currency (the index's) steers the
    venue. The pin's label / the membership constituent symbol is the underlying key the chain and
    the OHLC bars both store under, so a name's bars and surface share one ``underlying``.

    Returns a ``{constituent_symbol: CaptureTarget}`` map for the names whose conid resolved. A
    name whose conid will not resolve is **absent** from the map (the caller records it as an
    ``unresolved`` ledger outcome — never a silent drop). Each underlying appears at most once.
    """
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
) -> _ConstituentResult:
    """Attempt one constituent's option capture and return its labelled outcome.

    The four outcomes are kept distinct so the ledger answers the entitlement question per name
    rather than swallowing every failure into one ``None`` (the bug this replaces):

    * ``unresolved`` — no ``target`` (the conid would not resolve to a listing IBKR carries here);
    * ``unentitled`` — the transport refused the data with a 401/403 (the account is not entitled);
    * ``no_options`` — the name resolved and is entitled but lists no qualifiable options;
    * ``captured`` — the chain was captured (``n_options`` carries the option-leg count).

    A non-entitlement transport/discovery error is recorded as ``no_options`` with the error text
    in ``detail`` (the name is reachable but yielded no usable chain this run) — it never aborts
    the sweep; one bad name must not fail the fire.
    """
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
    """Turn the per-name results into the persisted ledger rows (one per attempted constituent)."""
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
    run_id: str | None = None,
) -> IndexBasket | None:
    """Capture the index *and* its top-N-by-weight constituents' option chains as one basket (§7.4).

    The widened EOD close capture: it banks the index's option chain (the spine — its failure
    fails the fire) and, on the same grid / selection / close instant, the option chains of the
    index's **point-in-time top-N constituents by index weight**
    (``config.universe.constituent_top_n``, resolved through the shared look-ahead-gated
    :func:`top_n_by_weight` over 1A membership weights — never a hand-set list, never today's
    membership for a past date). All captured legs are merged into one :class:`IndexBasket`, keyed
    by ``underlying``, which the underlying-generic ``run_analytics`` prices unchanged.

    **Every attempted constituent records a labelled outcome** (``captured`` / ``no_options`` /
    ``unentitled`` / ``unresolved``) in the ``constituent_capture_outcomes`` ledger, so the
    entitlement verdict for each name is persisted and visible (the capture-coverage panel).

    **Fail-loud, never silent.** If the index lists no options the lane returns ``None`` (the
    index-only lane's clean no-capture day — constituents are not swept, by design). Otherwise,
    with constituents in scope:

    * **no banked membership** for the index as of the trade date raises
      :class:`ConstituentLaneError` (CRITICAL): the silent empty basket that hid the canary is now
      a non-zero exit naming the missing input (ingest a weighted source via
      ``scripts/ingest_membership.py``);
    * **a basket with any missing weight** raises :class:`MembershipRankingError` from the shared
      resolver (also loud) — you cannot rank what you do not know;
    * if names resolved but **not a single one could even be attempted**, that too raises
      :class:`ConstituentLaneError`. Names that are attempted but yield no chain (unentitled / no
      options / unresolved) are a recorded outcome, not a failure.

    ``as_of`` is the index's own session close (every captured event is stamped there);
    ``next_open`` bounds the admitted close set to ``[as_of, next_open)`` (the look-ahead guard).
    ``store`` reads the as-of membership and persists the ledger. ``run_id`` keys the ledger rows
    (defaults to the close instant when the caller has no correlation id). The transport is
    injected so the capture runs on a fake.
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
    resolved_run_id = run_id if run_id is not None else as_of.isoformat()

    # The shared look-ahead-gated resolver. It raises MembershipRankingError on a missing-weight
    # basket (loud, propagated). An EMPTY basket — no banked membership for the trade date — is the
    # canary's exact gap: it is NOT an error from the resolver's view (nothing to rank), but for a
    # lane that has constituents in scope it is a missing precondition we must surface loudly.
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
        )
        for rank, member in enumerate(top_n, start=1)
    ]

    # Persist the per-name ledger BEFORE the guard, so even a fire that then fails loud leaves the
    # per-name verdicts on disk for triage (the entitlement question is answered regardless).
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

    # The guard the canary needed: constituents in scope ⇒ at least one constituent ATTEMPTED.
    # `results` has one entry per resolved top-N name (every name yields a labelled attempt), so an
    # empty `results` means we never reached a single name — a wiring failure, loud.
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
