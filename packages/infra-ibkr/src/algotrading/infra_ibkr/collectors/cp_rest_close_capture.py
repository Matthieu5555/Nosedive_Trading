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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import structlog
from algotrading.core.config import PlatformConfig, StrikeSelectionConfig
from algotrading.infra.actor import IndexBasket
from algotrading.infra.contracts import InstrumentKey, InstrumentMaster, RawMarketEvent
from algotrading.infra.snapshots import assess_quote
from algotrading.infra.surfaces import tenor_years as tenor_year_fraction
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
) -> tuple[AvailableChain, dict[str, str], dict[str, str]]:
    """Discover the listed chain for the underlying and build the broker-neutral ``AvailableChain``.

    Drives the CP three-step ``strikes`` → ``info`` sequence (the ``search`` already resolved
    the conid and the listed ``months``). Returns the assembled chain menu, a
    ``(expiry,strike,right) -> conid`` map so the capture stage can snapshot exactly the
    selected contracts by their resolved conid, and a ``token -> listing-month`` map so the
    discovery cache can record which IBKR month each contract listed under.

    Per expiry the qualified strike window is **delta-driven and tenor-aware**
    (:func:`qualify_strikes_for_expiry` → ``select_discovery_strikes``): it contains the
    30Δ band at that tenor (the band's strike width grows with √T), so the downstream economic
    selection can reach the true 30Δ strikes — never the ~ATM±1% sliver a fixed strike count
    delivered. ``info`` costs one paced call per (strike, right); the window is full-30Δ (no cap)
    but bounded in practice by the listed strikes, with the runaway valve as the only backstop.
    """
    log = _LOGGER.bind(underlying=target.symbol, as_of=as_of.isoformat())
    # Step 1 (sequential, one cheap call per month): resolve each month's listed strikes and
    # qualify the delta-driven window. The strike window selection is UNTOUCHED (owner ruling,
    # cp_rest_chain_window.py): this stage decides *which* (month, strike, right) items to walk.
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

    # Step 2 (bounded-concurrent): the per-(month, strike, right) `/secdef/info` walk. Each call is
    # ~all network wait and independent of the others, and the assembled chain is order-independent
    # (sorted-set expirations/strikes, a token-keyed conid dict), so a bounded ThreadPoolExecutor
    # yields a BYTE-IDENTICAL chain to the sequential walk — only faster. The transport's 429/503
    # backoff stays the pacing valve; the pool is small (typed config) so it degrades gracefully
    # rather than flooding the single paced CP Gateway session. A pool of 1 is the sequential walk.
    pool_size = min(max(strike_selection.discovery_pool_size, 1), max(len(work_items), 1))
    contracts = _qualify_contracts_concurrently(
        discovery,
        target=target,
        conid=conid,
        work_items=work_items,
        pool_size=pool_size,
        log=log,
    )

    # Step 3 (deterministic assembly): fold the results in a stable order so the output never
    # depends on completion order. Sorting the (token, contract) pairs makes the last-write of
    # `multiplier` deterministic too (every contract carries the same "100", so this is belt-and-
    # braces, not a behaviour change).
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
    """Rebuild the broker-neutral ``AvailableChain`` from a warm cache hit (no ``/secdef`` walk).

    The cached row holds the discovered expirations (``YYYYMMDD`` tokens) and strikes plus the
    listing multiplier, which is exactly the ``AvailableChain`` shape the live discovery assembles —
    so a warm hit reconstructs the identical menu the plan stage consumes, with zero broker calls.
    """
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
    """Run the per-(month, strike, right) ``/secdef/info`` walk through a bounded thread pool.

    Returns ``(listing_month, contract_token, contract)`` triples for every contract that qualified
    (carries a broker conid), to be folded into the chain in a deterministic order by the caller.
    The walk
    is order-independent, so concurrency changes only the wall-clock, never the assembled output.
    A pool of size 1 reproduces the sequential walk exactly. The transport's 429/503 backoff is
    the pacing valve; a worker that exhausts it raises, and that propagates (loud-fail discipline —
    a discovery that cannot resolve its chain is not a silently-thinner chain).
    """

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


@dataclass(frozen=True, slots=True)
class _PromotedSnapshots:
    """The outcome of the kept/drop + quote-integrity pass over one underlying's snapshot rows.

    ``events`` are the **faithful** ``RawMarketEvent`` rows for EVERY kept observation — both the
    healthy two-sided quotes and the quarantined (one-sided / non-positive / crossed) ones — so the
    raw layer records what the broker actually returned (blueprint 01-architecture §13/§39). The
    two-sided gate that keeps a quarantined row out of the IV solver is a *derived* concern applied
    downstream (``actor.driver``), not a raw-capture filter. ``option_row_count`` is how many option
    rows survived the look-ahead guard (the denominator of the two-sided fraction);
    ``two_sided_count`` is how many carried a healthy two-sided quote; ``drop_reasons`` is the
    receipt — one ``(instrument_key, reason)`` per quarantined option row — and the basket-integrity
    verdict thresholds on the resulting fraction. Quarantined rows are appended after the two-sided
    ones, so their inclusion is *additive* (the two-sided rows' event ids are unchanged).
    """

    events: tuple[RawMarketEvent, ...]
    option_row_count: int
    two_sided_count: int
    drop_reasons: tuple[tuple[str, str], ...]


def _two_sided_quote_reason(row: SnapshotRow, *, max_spread_pct: float) -> str | None:
    """``None`` when the row carries a healthy two-sided quote, else the drop reason code.

    Reuses the shared :func:`assess_quote` / :func:`QuoteAssessment` machinery (the leverage-proven
    rule — no parallel hand-rolled classifier): a row is promotable only when both sides are
    present, both strictly positive, and the market is not crossed. A zero/one-sided/crossed quote
    is what the closed-market canary banked (``bid==ask<=0``, ``completeness=0.333``), and is
    quarantined here. The reason code is the worst ``assess_quote`` reason (``crossed`` /
    ``non_positive_bid`` / ``non_positive_ask`` / ``missing_side``), so the audit receipt names
    *why* the row was not promoted, not merely that it was.
    """
    bid = row.bid
    ask = row.ask
    if bid is None or ask is None:
        return "missing_side"
    if ask <= 0.0:
        return "non_positive_ask"
    # The remaining classification (non-positive bid, crossed market) is exactly what assess_quote's
    # local checks already encode — reuse them rather than re-deriving the predicates.
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
    """Snapshot the selected contracts at the close, gate on quote integrity, normalize to events.

    Every promoted event is stamped at ``as_of`` (the session close) — both the exchange and
    receipt time — so the basket is the close set, byte-identical on replay. A snapshot row whose
    own update time (``_updated``) is at or after ``next_open`` (the next session's open) is
    dropped: it belongs to a later session, a wrong-day catch-up snapshot the close capture never
    folds in (the look-ahead guard). The admitted window is the half-open ``[as_of, next_open)``,
    so the post-close settlement marks the timer fires into — whose ``_updated`` is after ``as_of``
    but before the next open — are kept, not dropped. A row for an unrequested conid is ignored.

    On top of the look-ahead guard, the kept OPTION rows are classified by a **two-sided
    quote-integrity rule** (positive, uncrossed bid AND ask — :func:`assess_quote`): a healthy row
    counts toward ``two_sided_count``; a zero / single-sided / crossed row (the closed-market
    canary, ``bid==ask<=0`` with only ``last`` real) is recorded with a drop reason. **Both** are
    written to raw (faithful capture, blueprint 01-architecture §13/§39): the quarantined row is
    NOT erased — it is simply excluded from the IV solver downstream, in the derived layer
    (``actor.driver._has_two_sided_option_quote``), the one rule the live and replay paths share
    (ADR 0027). The underlying leg always anchors the chain's reference spot.

    ``sequence`` is assigned from the kept contracts' stable canonical order (two-sided first,
    quarantined appended), NOT the broker's response row order: a re-fire / retry that returns the
    same contracts in a different order yields identical content-addressed event ids, so the
    append-only store dedupes the re-capture; appending the quarantine rows leaves the two-sided
    rows' ids unchanged. Broker-supplied ``conid`` / ``_updated`` scalars coerce through the wire
    model's validators, so an unexpected payload shape skips the row rather than raising.
    """
    if not keys_by_conid:
        return _PromotedSnapshots(events=(), option_row_count=0, two_sided_count=0, drop_reasons=())
    # Warm-up polled like the spot snapshot: a cold first call returns metadata-only rows (no
    # marks), which would yield a basket of contracts with no quotes — IV/Greeks could not price.
    rows = snapshot_with_warmup(transport, conids=sorted(keys_by_conid), warmup=warmup)
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
    # Second pass: classify each kept OPTION row by the two-sided quote-integrity rule, recording
    # a drop reason for the rest (the audit receipt) and the two-sided fraction the basket verdict
    # thresholds on. The underlying leg is always healthy here (its quote anchors the spot, not an
    # option mark).
    #
    # Raw is FAITHFUL (blueprint 01-architecture §13/§39: "no downstream layer may silently
    # overwrite an upstream observation"; the collector "writes [the ticks] to the raw layer").
    # Every kept row — two-sided AND quarantined — is normalized to events and written to raw. The
    # two-sided gate is a DERIVED concern, applied where the IV solver selects its inputs
    # (`actor.driver._has_two_sided_option_quote`), shared by the live and replay paths (ADR 0027),
    # so a quarantined row never reaches the solver yet is reproducible from raw. Sequence keeps the
    # two-sided rows in their stable canonical order FIRST (so their content-addressed event ids are
    # unchanged — the quarantine rows are appended, an additive raw gain), each contract's events
    # sharing its ordinal; a shuffled re-fire of the same set reproduces the same ids.
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
    for sequence, (instrument, row) in enumerate([*two_sided, *quarantined]):
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
    """Get the chain + conid map from cache when fresh (lever B/C), else from a live secdef walk.

    Warm hit: rebuild the ``AvailableChain`` and ``token -> conid`` map from the cached row — ZERO
    ``/secdef`` calls. With ``revalidate_cached_conids`` the cached conids are first bulk-checked
    via ``/trsrv/secdef`` (200/call) and any conid the gateway no longer lists is dropped; if the
    warm hit survives with at least one conid it is used, else it falls through to a live walk.
    Miss / staleness: run the live discovery and, when a cache is supplied, persist the result keyed
    on ``as_of`` so the next fire is warm. The live walk's behaviour is unchanged.
    """
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
    """Drop cached contracts whose conid ``/trsrv/secdef`` no longer lists (lever C, 200/call)."""
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

    A **quote-integrity verdict** (EMERGENCY-quote-integrity-gate) is logged on top: each kept
    option row is classified, the two-sided fraction is computed, and a closed / last-only /
    degenerate basket below ``config.qc_threshold.quote_integrity``'s two-sided floor (the
    2026-06-15 SX5E canary) is flagged loudly. The verdict no longer drops anything: the faithful
    basket — EVERY observed row — is returned so it lands in raw (blueprint 01-architecture §13).
    A degenerate basket then fails LOUD by construction (the derived two-sided gate admits no option
    to the IV solver → empty grid → QC coverage page → non-zero exit), never a silent surface fit
    off last-only marks and never the old silent ``None`` that dropped the marks and exited 0.
    ``None`` is now returned ONLY when the underlying lists no qualifiable option chain at all.

    **Discovery cache (speed lever B/C).** The ``(underlying, month, strike, right) -> conid`` map
    is static, so when a ``discovery_cache`` is supplied and holds a fresh entry for the underlying,
    the live ``/secdef`` walk (hundreds of paced calls) is SKIPPED and the chain + conid map are
    rebuilt from cache, going straight to the cheap batched snapshot. A cache miss / staleness falls
    back to the live walk, whose result is then stored for the next fire. With
    ``revalidate_cached_conids=True`` the cached conids are bulk-checked via ``/trsrv/secdef``
    (200/call) before snapshotting and any delisted conid is dropped. ``warmup`` tunes the snapshot
    cold-warm poll budget (speed lever E); all three default off / to the legacy behaviour, so
    existing callers are unchanged.
    """
    log = _LOGGER.bind(underlying=target.symbol, as_of=as_of.isoformat())
    selection = selection or _selection_from_config(config, as_of.date())
    discovery = CpRestDiscovery(
        transport, exchange=target.exchange, currency=target.currency
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
        # Contracts came back but every row was dropped as a later session: a wrong-day / wrong-time
        # capture, not a clean optionless no-op (that returned None far above). Fail loud so the
        # runner exits non-zero and OnFailure= alerts, rather than silently landing an empty day.
        raise CloseCaptureError(
            f"{target.symbol}: snapshot returned {len(kept_options)} option contracts but kept 0 "
            f"rows after the look-ahead guard (as_of={as_of.isoformat()}, "
            f"next_open={next_open.isoformat()}) — empty close set, refusing to land it silently"
        )
    # Basket-level quote-integrity verdict (EMERGENCY-quote-integrity-gate). A closed / last-only /
    # degenerate basket — too few rows carrying a healthy two-sided quote (the 2026-06-15 SX5E
    # canary, every row bid==ask<=0 with only `last` real) — must NOT bank a surface fit off
    # last-only marks. But the verdict is no longer a *capture-layer drop*: per the blueprint
    # (01-architecture §13/§39 — a downstream concern must not erase an upstream observation), the
    # faithful basket is still returned so EVERY observed row lands in raw. The degenerate close
    # then fails LOUD downstream by construction: the derived two-sided gate
    # (`actor.driver._has_two_sided_option_quote`) admits no option to the IV solver, so the grid is
    # empty, the QC coverage-floor checks fail, QC escalates to `page`, and the runner exits
    # non-zero (OnFailure= alerts) — instead of the old silent ``None`` no-capture that dropped the
    # marks AND exited 0. The wrong-day ``CloseCaptureError`` above stays a hard raise (look-ahead,
    # not a market verdict). The verdict is logged loudly here for the operator triage trail.
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
        discovery_cache=discovery_cache,
        revalidate_cached_conids=revalidate_cached_conids,
        warmup=warmup,
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
