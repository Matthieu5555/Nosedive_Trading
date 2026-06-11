"""``collect_live`` — the real EOD close basket source over CP REST (ADR 0024/0031, WS 1C).

The EOD spine exposes one transport-agnostic injection point — the ``BasketSource`` the runner
threads into ``default_stages_builder`` — and its production default returns ``None`` (the clean
no-capture day) until a live source lands. This module is that source. Given an authenticated,
OAuth-signed :class:`CpRestTransport` and a fired index, it captures the index's EOD close basket
and returns the populated :class:`IndexBasket` the downstream analytics / ``project_grid`` /
persist stages already consume.

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
own ``FiredIndex.as_of`` (the resolver's ``session_close``), and a snapshot row carrying an
update time *after* that close is dropped (never folded into the close basket) — the capture
reads the close, never a post-close print. Pure given the transport's responses; the only clock
read is the injected ``as_of``.

Transport stays on CP REST (the settled decision, ADR 0024/0031): no Nautilus ``TradingNode`` is
introduced for capture. The HTTP layer is the injected transport, so the gate drives the whole
capture against a fake gateway with no network and no secrets.
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, Protocol

import structlog
from algotrading.core.config import PlatformConfig
from algotrading.infra.actor import IndexBasket
from algotrading.infra.contracts import InstrumentKey, InstrumentMaster, RawMarketEvent
from algotrading.infra.universe import (
    AvailableChain,
    ChainSelection,
    IndexEntry,
    plan_chain,
    select_capture_keys,
)

from .cp_rest_discovery import CpRestDiscovery
from .cp_rest_index import resolve_index
from .cp_rest_normalize import REQUEST_FIELD_TAGS, snapshot_to_events

_LOGGER = structlog.get_logger("ibkr.close_capture")

# The index itself is a non-option underlying; its security type in our key space. The option
# multiplier IBKR lists is a string ("100"); the index leg carries a multiplier of 1.0 (it is
# not a contract with a lot size in our key space, only the options are).
_INDEX_SECURITY_TYPE = "IND"
_INDEX_MULTIPLIER = 1.0
_OPTION_SECURITY_TYPE = "OPT"

# Discovery request-shaping bound (broker-side, not economic): how many near-the-money strikes
# per side to *qualify* per expiry. Conid resolution costs one `/iserver/secdef/info` call per
# (strike, right) — IBKR rejects a batch `info` with no strike ("strike is required") — so a dense
# index ladder (ESTX50 lists ~300 strikes/expiry in 25-pt steps) would otherwise mean thousands of
# serial paced calls. We qualify only the nearest-the-money block, a superset of the 30Δ band the
# analytics stage selects downstream; the far wings it would drop anyway are never qualified. This
# is the same category as `ChainSelection.strike_window_pct` (a request-shaping heuristic that
# "lives in code"), tightened to a strike *count* because a %-of-spot window does not bound a
# dense ladder. Keep it a clear superset of the downstream band — see chain_planning.select_strikes.
_DISCOVERY_STRIKES_PER_SIDE = 16


class _SupportsGet(Protocol):
    def get(self, path: str, params: dict[str, Any] | None = None) -> Any: ...


def _as_int_or_none(value: object) -> int | None:
    """Coerce a broker-supplied scalar to ``int``, or ``None`` when it is not coercible.

    The broker's conid / ``_updated`` fields are nominally integers but ride an untyped JSON
    payload, so an unexpected shape (``None``, a non-numeric string, a dict) must degrade to a
    structured skip at the call site rather than raise a bare ``ValueError`` and abort the whole
    capture — mirroring the guarded ``float()`` parsing of the mark fields. ``bool`` is rejected
    because a JSON ``true``/``false`` is never a valid conid or millisecond timestamp.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


# IBKR's `/iserver/marketdata/snapshot` returns only field *metadata* (server ids, request echo)
# on the first call(s) for a freshly-subscribed conid; the requested value tags (last/bid/ask/…)
# populate only once the server-side market-data line warms — typically a second or two later. A
# single un-retried call is exactly why a cold capture saw `spot=None` and then selected zero
# options. So we poll the same request until the values appear. Bounded by `_..._ATTEMPTS` so an
# illiquid contract that never prints cannot hang the fire, and the loop stops early once the
# populated set stops growing (converged) — the dead wings won't print, no point waiting on them.
_SNAPSHOT_WARMUP_ATTEMPTS = 8
_SNAPSHOT_WARMUP_SLEEP_S = 1.0

# IBKR's snapshot is a GET carrying the conids in the query string. A full index chain is hundreds
# of contracts, and that many conids overflow the gateway's request-URI length limit (HTTP 414 —
# the failure a real ESTX50 capture hit once spot resolved and the whole chain was discovered). So
# the request is split into URI-safe batches and the rows concatenated; each batch is independently
# warm-up polled. 50 conids ≈ a 600-char URL, comfortably under the limit and well within IBKR's
# documented per-request conid cap.
_SNAPSHOT_MAX_CONIDS = 50


def _row_has_value(row: Mapping[str, object]) -> bool:
    """True when a snapshot row carries at least one parseable market-data value tag.

    The warm/cold discriminator: a cold row carries only metadata (``conid``, ``server_id``,
    field-availability flags), no value tag; a warm row carries last/bid/ask/size. A tag counts as
    present when it parses to a float (after stripping a leading status flag like ``C``/``H``),
    mirroring the normalizer's own parse so "populated" here means "will yield an event".
    """
    for tag in REQUEST_FIELD_TAGS:
        value = row.get(tag)
        if value is None:
            continue
        try:
            float(str(value).lstrip("CHch").strip())
        except ValueError:
            continue
        return True
    return False


def _populated_conids(rows: object, requested: frozenset[int]) -> set[int]:
    """The subset of ``requested`` conids whose snapshot row carries a parseable value tag."""
    populated: set[int] = set()
    if not isinstance(rows, Sequence):
        return populated
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        conid = _as_int_or_none(row.get("conid"))
        if conid is None or conid not in requested:
            continue
        if _row_has_value(row):
            populated.add(conid)
    return populated


def _warmup_poll_batch(transport: _SupportsGet, batch: Sequence[int]) -> list[Any]:
    """Warm-up poll ONE URI-safe batch of conids; return its snapshot rows (possibly empty).

    Issues the same ``/iserver/marketdata/snapshot`` request up to ``_SNAPSHOT_WARMUP_ATTEMPTS``
    times, returning as soon as every conid in the batch carries a value tag (fully warm) or the
    populated set stops growing between two polls (converged — the rest are illiquid and won't
    print). On a gateway that already returns values on the first call this returns immediately
    with a single request and no sleep; on a cold subscription it pays a few short polls so the
    capture sees real marks instead of an empty first response.
    """
    requested = frozenset(batch)
    params = {
        "conids": ",".join(str(conid) for conid in sorted(requested)),
        "fields": ",".join(REQUEST_FIELD_TAGS),
    }
    rows = transport.get("/iserver/marketdata/snapshot", params=params)
    populated = _populated_conids(rows, requested)
    for _attempt in range(_SNAPSHOT_WARMUP_ATTEMPTS - 1):
        if populated == requested:
            break  # every requested conid is warm — nothing left to wait for
        time.sleep(_SNAPSHOT_WARMUP_SLEEP_S)
        rows = transport.get("/iserver/marketdata/snapshot", params=params)
        next_populated = _populated_conids(rows, requested)
        if next_populated and next_populated <= populated:
            break  # no new conid warmed since the last poll — converged, stop polling
        populated = next_populated
    if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)):
        return list(rows)
    return []


def _snapshot_with_warmup(transport: _SupportsGet, *, conids: Sequence[int]) -> list[Any]:
    """Snapshot the conids in URI-safe batches (each warm-up polled) and concatenate the rows.

    A full chain's conids overflow the snapshot GET's URI length (HTTP 414), so the request is
    split into ``_SNAPSHOT_MAX_CONIDS``-sized batches; :func:`_warmup_poll_batch` handles the
    cold-snapshot warm-up per batch. Deterministic order: conids are sorted, then batched.
    """
    ordered = sorted(frozenset(conids))
    rows: list[Any] = []
    for start in range(0, len(ordered), _SNAPSHOT_MAX_CONIDS):
        rows.extend(_warmup_poll_batch(transport, ordered[start : start + _SNAPSHOT_MAX_CONIDS]))
    return rows


def _index_key(index: IndexEntry, conid: int) -> InstrumentKey:
    """The index underlying's canonical :class:`InstrumentKey` (the chain's centre)."""
    return InstrumentKey(
        underlying_symbol=index.symbol,
        security_type=_INDEX_SECURITY_TYPE,
        exchange=index.ibkr.exchange,
        currency=index.currency,
        multiplier=_INDEX_MULTIPLIER,
        broker_contract_id=str(conid),
    )


def _option_key(
    index: IndexEntry, *, expiry: date, strike: float, right: str, multiplier: float, conid: str
) -> InstrumentKey:
    """One option contract's canonical :class:`InstrumentKey`, carrying its IBKR conid."""
    return InstrumentKey(
        underlying_symbol=index.symbol,
        security_type=_OPTION_SECURITY_TYPE,
        exchange=index.ibkr.exchange,
        currency=index.currency,
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


def _spot_from_snapshot(
    rows: object, *, conid: int
) -> float | None:
    """Pull the index level (last, else bid/ask mid) from a snapshot response for one conid.

    Used only to centre the discovery strike window — a request-shaping number, not an
    observation persisted anywhere. ``None`` when the row is absent or unparseable, in which
    case :func:`plan_chain` falls back to its spot-less (median-strike) window.
    """
    if not isinstance(rows, Sequence):
        return None
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        row_conid = _as_int_or_none(row.get("conid"))
        if row_conid is None or row_conid != conid:
            continue
        for tag in ("31", "84", "86"):  # last, bid, ask
            value = row.get(tag)
            if value is None:
                continue
            try:
                parsed = float(str(value).lstrip("CHch").strip())
            except ValueError:
                continue
            if parsed > 0.0:
                return parsed
    return None


def _snapshot_index_spot(transport: _SupportsGet, conid: int) -> float | None:
    """REST snapshot the index level to centre the chain window (request-shaping only).

    Warm-up polled (:func:`_snapshot_with_warmup`): the index's first cold snapshot carries no
    value tag, so a single call would return ``spot=None`` and collapse the downstream selection.
    """
    rows = _snapshot_with_warmup(transport, conids=(conid,))
    return _spot_from_snapshot(rows, conid=conid)


def _nearest_strikes(
    strikes: set[float], spot: float | None, per_side: int
) -> list[float]:
    """The nearest-the-money block to qualify: up to ``per_side`` strikes either side of spot.

    Bounds the per-(strike, right) ``/secdef/info`` qualification cost on a dense ladder (see
    ``_DISCOVERY_STRIKES_PER_SIDE``). Strikes are ranked by absolute distance to the centre and the
    nearest ``2 * per_side`` are kept, then returned ascending. The centre is the snapshot ``spot``
    when usable; with no spot it falls back to the median listed strike — bounded and deterministic,
    just not centred on the true forward — mirroring ``chain_planning._strikes_by_moneyness``. A
    sparse name with fewer than ``2 * per_side`` listed strikes simply qualifies all of them.
    """
    positive = sorted({float(strike) for strike in strikes if float(strike) > 0.0})
    if not positive:
        return []
    if spot is not None and math.isfinite(spot) and spot > 0.0:
        centre = spot
    else:
        centre = positive[len(positive) // 2]
    nearest = sorted(positive, key=lambda strike: (abs(strike - centre), strike))[: 2 * per_side]
    return sorted(nearest)


def _discover_chain(
    discovery: CpRestDiscovery,
    *,
    index: IndexEntry,
    conid: int,
    months: Sequence[str],
    selection: ChainSelection,
    spot: float | None,
) -> tuple[AvailableChain, dict[str, str]]:
    """Discover the listed chain for the index and build the broker-neutral ``AvailableChain``.

    Drives the CP three-step ``strikes`` → ``info`` sequence (the ``search`` already resolved
    the conid and the listed ``months``). Returns the assembled chain menu *and* a
    ``(expiry,strike,right) -> conid`` map so the capture stage can snapshot exactly the
    selected contracts by their resolved conid.

    Per expiry, only the nearest-the-money block (:func:`_nearest_strikes`, ±
    ``_DISCOVERY_STRIKES_PER_SIDE``) is qualified: ``info`` costs one paced call per
    (strike, right), so qualifying a dense ladder's full width is thousands of serial calls.
    The kept block is a superset of the downstream 30Δ band; the far wings it would drop are
    never qualified.
    """
    expirations: list[str] = []
    strikes: set[float] = set()
    conid_by_contract: dict[str, str] = {}
    multiplier = "100"
    for month in months[: selection.max_expiries]:
        calls, puts = discovery.strikes(conid, month=month)
        listed = set(calls) | set(puts)
        for strike in _nearest_strikes(listed, spot, _DISCOVERY_STRIKES_PER_SIDE):
            for right in ("C", "P"):
                for contract in discovery.contracts(
                    conid, symbol=index.ibkr_search_symbol, month=month, strike=strike, right=right
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
        exchange=index.ibkr.exchange,
        trading_class=index.symbol,
        multiplier=multiplier,
        expirations=tuple(sorted(set(expirations))),
        strikes=tuple(sorted(strikes)),
    )
    return chain, conid_by_contract


def _contract_token(expiry: date, strike: float, right: str) -> str:
    """A stable key into the conid map for one (expiry, strike, right)."""
    return f"{expiry.isoformat()}|{strike:.10g}|{right}"


def _planned_option_keys(
    index: IndexEntry,
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
                        index,
                        expiry=expiry,
                        strike=strike,
                        right=right,
                        multiplier=multiplier,
                        conid=conid,
                    )
                )
    return keys


def _snapshot_events(
    transport: _SupportsGet,
    *,
    keys_by_conid: Mapping[int, InstrumentKey],
    underlying: str,
    session_id: str,
    as_of: datetime,
) -> list[RawMarketEvent]:
    """Snapshot the selected contracts at the close and normalize to ``RawMarketEvent`` rows.

    Every event is stamped at ``as_of`` (the session close) — both the exchange and receipt time
    — so the basket is the close set, byte-identical on replay. A snapshot row whose own update
    time (``_updated``) is *after* the close is dropped: the close capture never folds a
    post-close print (the look-ahead guard). A row for an unrequested conid is ignored.

    ``sequence`` is assigned from the kept contracts' *stable identity* (their canonical instrument
    key), NOT from the broker's response row order: a re-fire / retry that returns the same
    contracts in a different order must yield identical content-addressed event ids, so the
    append-only store dedupes the re-capture instead of keeping a second copy. Broker-supplied
    ``conid`` / ``_updated`` scalars are coerced through :func:`_as_int_or_none`, so an unexpected
    payload shape skips the row with a structured log rather than raising a bare ``ValueError``.
    """
    if not keys_by_conid:
        return []
    # Warm-up polled like the spot snapshot: a cold first call returns metadata-only rows (no
    # marks), which would yield a basket of contracts with no quotes — IV/Greeks could not price.
    rows = _snapshot_with_warmup(transport, conids=sorted(keys_by_conid))
    close_ms = int(as_of.timestamp() * 1000)
    if not isinstance(rows, Sequence):
        return []
    # First pass: keep the admitted (instrument, row) pairs, dropping unrequested conids, malformed
    # payloads, and post-close prints. Sequence is NOT assigned here — row order is not trusted.
    kept: list[tuple[InstrumentKey, Mapping[str, object]]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        row_conid = _as_int_or_none(row.get("conid"))
        if row_conid is None:
            continue
        instrument = keys_by_conid.get(row_conid)
        if instrument is None:
            continue
        updated = _as_int_or_none(row.get("_updated"))
        if updated is not None and updated > close_ms:
            # A print stamped after the session close is post-close data — never in the basket.
            _LOGGER.info(
                "ibkr.close_capture.drop_post_close",
                conid=row_conid,
                updated_ms=updated,
                close_ms=close_ms,
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


def collect_live_basket(
    transport: _SupportsGet,
    *,
    index: IndexEntry,
    as_of: datetime,
    config: PlatformConfig,
    selection: ChainSelection | None = None,
) -> IndexBasket | None:
    """Capture one fired index's EOD close basket over CP REST (the live ``BasketSource`` body).

    Resolves the index conid from its symbol, snapshots its spot to centre the chain, discovers
    and plans the option chain, caps it to the capture budget, snapshots the selected contracts
    at the close, and returns the populated :class:`IndexBasket`. Returns ``None`` (a clean,
    labeled empty capture — never a raise) only when the index lists no option chain at all, so
    a name with no listed options degrades to a no-capture day rather than failing the fire.

    ``selection`` defaults to a :class:`ChainSelection` built from the universe config's strike-
    selection knobs (nearest maturities, the per-session strike budget); the economic 30Δ band
    runs downstream in :func:`run_analytics`. ``as_of`` is the index's own session close — every
    captured event is stamped there and no post-close print is admitted.
    """
    log = _LOGGER.bind(index=index.symbol, as_of=as_of.isoformat())
    resolved = resolve_index(
        transport, symbol=index.ibkr_search_symbol, exchange=index.ibkr.exchange
    )
    conid = resolved.conid
    selection = selection or _selection_from_config(config)
    discovery = CpRestDiscovery(
        transport, exchange=index.ibkr.exchange, currency=index.currency
    )
    spot = _snapshot_index_spot(transport, conid)
    chain, conid_by_contract = _discover_chain(
        discovery,
        index=index,
        conid=conid,
        months=resolved.option_months,
        selection=selection,
        spot=spot,
    )
    if not conid_by_contract:
        log.info("ibkr.close_capture.no_options", reason="index lists no qualifiable options")
        return None

    plan = plan_chain(index.symbol, [chain], spot=spot, selection=selection)
    if plan is None:
        log.info("ibkr.close_capture.no_plan", reason="no listing selected for the index")
        return None

    multiplier = float(plan.multiplier) if plan.multiplier else 100.0
    index_key = _index_key(index, conid)
    option_keys = _planned_option_keys(
        index,
        plan_expiries=plan.expiries,
        plan_strikes=plan.strikes,
        plan_rights=plan.rights,
        multiplier=multiplier,
        conid_by_contract=conid_by_contract,
    )

    # Cap to the per-session capture budget (nearest-the-money), then snapshot exactly those.
    spots = {index.symbol: spot} if spot is not None else {}
    captured = set(
        select_capture_keys(
            [index_key, *option_keys],
            spots=spots,
            selection=selection,
            exchange=index.ibkr.exchange,
        )
    )
    kept_options = [key for key in option_keys if key.canonical() in captured]
    keys_by_conid: dict[int, InstrumentKey] = {conid: index_key}
    for key in kept_options:
        option_conid = _as_int_or_none(key.broker_contract_id)
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

    session_id = f"{index.symbol}:{as_of.date().isoformat()}"
    events = _snapshot_events(
        transport,
        keys_by_conid=keys_by_conid,
        underlying=index.symbol,
        session_id=session_id,
        as_of=as_of,
    )

    instruments = (index_key, *kept_options)
    masters = tuple(_master(key, as_of) for key in instruments)
    log.info(
        "ibkr.close_capture.captured",
        conid=conid,
        option_count=len(kept_options),
        event_count=len(events),
        spot=spot,
    )
    return IndexBasket(
        instruments=instruments, events=tuple(events), masters=masters
    )


def _selection_from_config(config: PlatformConfig) -> ChainSelection:
    """Build the capture :class:`ChainSelection` from the universe strike-selection config.

    The maturity budget and per-side floor are economic and come from the typed
    ``universe.yaml`` (never a ``.py`` literal): the nearest-maturity count and the per-side
    minimum mirror the strike-selection block. The %-of-spot window and option exchange keep
    their request-shaping defaults (a discovery heuristic, not an economic parameter).
    """
    strike_selection = config.universe.strike_selection
    return ChainSelection(
        max_expiries=len(config.universe.tenor_grid),
        min_strikes_per_side=strike_selection.min_strikes_per_side,
        option_exchange=config.universe.exchange,
    )
