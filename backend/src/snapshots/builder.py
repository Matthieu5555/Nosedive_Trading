"""Assemble a :class:`MarketStateSnapshot` for one instrument from its raw events.

This is the pure heart of step 5: given one instrument's events and an as-of
instant, read the latest fields (:mod:`snapshots.as_of`), choose a labeled
reference spot (:mod:`snapshots.reference_spot`), set the state flags, stamp the
result, and hand back the typed snapshot. No I/O, no wall clock — ``calc_ts`` is
injected, so the same events at the same instant always produce the same snapshot.

When an instrument has no honest reference spot (no quote, no last, no fallback),
``build_snapshot`` raises :class:`InsufficientSnapshotData` rather than inventing a
zero; :func:`build_snapshots` collects those as labeled skips so the gap is
queryable instead of silent.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from config import QcThresholdConfig
from contracts import MarketStateSnapshot, RawMarketEvent
from contracts.instrument_key import InstrumentKey
from provenance import ProvenanceStamp, source_ref, stamp

from .as_of import latest_by_field_before
from .quote_quality import QuoteAssessment, assess_quote
from .reference_spot import NoReferenceSpot, resolve_reference_spot

# Bump only on a real change to the snapshot logic, never on config.
SNAPSHOT_VERSION = "snapshot-1.0.0"
_QUOTE_FIELDS = ("bid", "ask", "last")


class InsufficientSnapshotData(Exception):
    """No honest reference spot could be built for an instrument at the instant.

    Carries the instrument key and a plain-language reason, so a caller logs which
    instrument was skipped and why instead of silently dropping it.
    """

    def __init__(self, instrument_key: str, reason: str) -> None:
        self.instrument_key = instrument_key
        self.reason = reason
        super().__init__(f"{instrument_key}: {reason}")


@dataclass(frozen=True, slots=True)
class SnapshotContext:
    """Everything the builder needs beyond the events themselves.

    The thresholds come from A's config (``qc``); ``calc_ts`` and ``config_hash``
    feed the provenance stamp; ``session_open`` is the venue session state; the
    prior close and prior spot are the fallback rungs; ``underlying_stale`` flags an
    option whose underlying's own quote is stale.

    ``prior_close`` and ``prior_spot`` must be point-in-time values known at or
    before ``snapshot_ts`` — the builder reads them as given, so the caller owns
    that as-of guarantee (see ``resolve_reference_spot``).
    """

    snapshot_ts: datetime
    qc: QcThresholdConfig
    calc_ts: datetime
    config_hash: str
    session_open: bool = True
    prior_close: float | None = None
    prior_spot: float | None = None
    underlying_stale: bool = False


@dataclass(frozen=True, slots=True)
class SkippedInstrument:
    """An instrument that could not be snapshotted, and why."""

    instrument_key: str
    reason: str


@dataclass(frozen=True, slots=True)
class AssessedSnapshot:
    """One snapshot paired with its quote-quality verdict (step 7).

    The snapshot is always built when an honest reference spot exists; the
    ``assessment`` is the separate QC axis (``usable``/``caution``/``reject`` plus
    every reason code) that decides whether downstream analytics should trust it.
    A's :class:`MarketStateSnapshot` carries no QC field, so the verdict rides
    alongside here — the same split forwards use between the rich in-memory
    estimate and the flat persisted contract.
    """

    snapshot: MarketStateSnapshot
    assessment: QuoteAssessment


@dataclass(frozen=True, slots=True)
class SnapshotBatch:
    """The snapshots built for a set of instruments, each with its QC verdict.

    ``assessed`` is the full set — every snapshot that had an honest reference spot,
    paired with its quote-quality verdict; ``skipped`` are the instruments with no
    spot at all. The full and filtered views are both kept (step 7, so QC is
    auditable): :attr:`snapshots` is every built snapshot regardless of verdict, and
    :attr:`usable` is the QC-filtered subset that downstream forward/IV code should
    consume. A rejected quote still appears in ``assessed``/``snapshots`` with its
    reason codes — the filter is queryable, never a silent drop.
    """

    assessed: tuple[AssessedSnapshot, ...]
    skipped: tuple[SkippedInstrument, ...]

    @property
    def snapshots(self) -> tuple[MarketStateSnapshot, ...]:
        """The full set of built snapshots, in build order, regardless of verdict."""
        return tuple(item.snapshot for item in self.assessed)

    @property
    def usable(self) -> tuple[MarketStateSnapshot, ...]:
        """The QC-filtered snapshots: those whose quote verdict is not ``reject``."""
        return tuple(
            item.snapshot for item in self.assessed if item.assessment.is_usable
        )


def _flags(
    instrument: InstrumentKey, context: SnapshotContext, is_fallback: bool, is_stale: bool
) -> tuple[str, ...]:
    """Assemble the state flags. Every condition is labeled, none implied."""
    flags = ["open" if context.session_open else "closed"]
    if is_stale:
        flags.append("stale_option" if instrument.is_option() else "stale_underlying")
    if instrument.is_option() and context.underlying_stale:
        flags.append("stale_underlying")
    if is_fallback:
        flags.append("fallback_spot")
    return tuple(flags)


def _stamp_for(
    used_events: list[RawMarketEvent], context: SnapshotContext
) -> ProvenanceStamp:
    """Stamp the snapshot with the raw events that fed it, in full-key lineage."""
    refs = tuple(
        source_ref("raw_market_events", event.session_id, event.event_id)
        for event in used_events
    )
    timestamps = tuple(event.canonical_ts for event in used_events)
    return stamp(
        calc_ts=context.calc_ts,
        code_version=SNAPSHOT_VERSION,
        config_hash=context.config_hash,
        source_records=refs,
        source_timestamps=timestamps,
    )


def _build_assessed(
    instrument: InstrumentKey,
    events: Sequence[RawMarketEvent],
    *,
    context: SnapshotContext,
) -> AssessedSnapshot:
    """Build one snapshot and run quote QC against the *same* observed inputs.

    The QC verdict is assessed from the raw observed ``bid``/``ask`` (``None`` when a
    field is absent) and the quote age — never from the projected snapshot fields,
    which store ``0.0`` for an absent side and would otherwise read as a locked or
    non-positive quote. Assessing here, beside the staleness decision, keeps the
    verdict consistent with the snapshot's own flags by construction. Raises
    :class:`InsufficientSnapshotData` if no honest reference spot can be derived.
    """
    key = instrument.canonical()
    own_events = [event for event in events if event.instrument_key == key]
    latest = latest_by_field_before(own_events, context.snapshot_ts)

    def field_value(name: str) -> float | None:
        found = latest.get(name)
        return found.value if found is not None else None

    bid = field_value("bid")
    ask = field_value("ask")
    try:
        reference = resolve_reference_spot(
            bid=bid,
            ask=ask,
            last=field_value("last"),
            prior_close=context.prior_close,
            prior_spot=context.prior_spot,
        )
    except NoReferenceSpot as exc:
        raise InsufficientSnapshotData(key, exc.reason) from exc

    used_events = list(latest.values())
    newest_ts = max((event.canonical_ts for event in used_events), default=None)
    age_seconds = (
        (context.snapshot_ts - newest_ts).total_seconds() if newest_ts is not None else None
    )
    is_stale = age_seconds is not None and age_seconds > context.qc.max_quote_age_seconds
    completeness = sum(1 for field in _QUOTE_FIELDS if field in latest) / len(_QUOTE_FIELDS)
    trade_date = (
        used_events[0].trade_date
        if used_events
        else context.snapshot_ts.astimezone(UTC).date()
    )

    snapshot = MarketStateSnapshot(
        snapshot_ts=context.snapshot_ts,
        instrument_key=key,
        reference_spot=reference.value,
        bid=reference.bid,
        ask=reference.ask,
        last=reference.last,
        spread_pct=reference.spread_pct,
        reference_type=reference.reference_type,
        flags=_flags(instrument, context, reference.is_fallback, is_stale),
        completeness=completeness,
        trade_date=trade_date,
        underlying=instrument.underlying_symbol,
        provenance=_stamp_for(used_events, context),
    )
    assessment = assess_quote(
        bid=bid,
        ask=ask,
        max_spread_pct=context.qc.max_spread_pct,
        age_seconds=age_seconds,
        max_quote_age_seconds=context.qc.max_quote_age_seconds,
    )
    return AssessedSnapshot(snapshot=snapshot, assessment=assessment)


def build_snapshot(
    instrument: InstrumentKey,
    events: Sequence[RawMarketEvent],
    *,
    context: SnapshotContext,
) -> MarketStateSnapshot:
    """Build one instrument's snapshot at ``context.snapshot_ts``.

    ``events`` may contain other instruments' events; they are filtered out by
    canonical key. Raises :class:`InsufficientSnapshotData` if no reference spot can
    be derived. Use :func:`assess_snapshot` when the quote-quality verdict is needed
    alongside the snapshot.
    """
    return _build_assessed(instrument, events, context=context).snapshot


def assess_snapshot(
    instrument: InstrumentKey,
    events: Sequence[RawMarketEvent],
    *,
    context: SnapshotContext,
) -> AssessedSnapshot:
    """Build one snapshot and its quote-quality verdict together (step 7).

    The QC-aware single-instrument path: the returned :class:`AssessedSnapshot`
    carries the same snapshot :func:`build_snapshot` would produce, plus the
    ``usable``/``caution``/``reject`` verdict and reason codes a consumer needs to
    decide whether to trust it. Raises :class:`InsufficientSnapshotData` on no spot.
    """
    return _build_assessed(instrument, events, context=context)


def build_snapshots(
    instruments: Sequence[InstrumentKey],
    events: Sequence[RawMarketEvent],
    *,
    snapshot_ts: datetime,
    qc: QcThresholdConfig,
    calc_ts: datetime,
    config_hash: str,
    session_open: bool = True,
    prior_closes: Mapping[str, float] | None = None,
    prior_spots: Mapping[str, float] | None = None,
) -> SnapshotBatch:
    """Build snapshots for a set of instruments, propagating underlying staleness.

    Underlyings are built first so an option can be told whether its underlying's
    own quote is stale (the cross-instrument ``stale_underlying`` flag). Instruments
    with no honest reference spot are collected as labeled skips, not dropped.
    """
    prior_closes = dict(prior_closes or {})
    prior_spots = dict(prior_spots or {})
    assessed: list[AssessedSnapshot] = []
    skipped: list[SkippedInstrument] = []
    stale_underlyings: set[str] = set()

    def context_for(instrument: InstrumentKey, *, underlying_stale: bool) -> SnapshotContext:
        key = instrument.canonical()
        return SnapshotContext(
            snapshot_ts=snapshot_ts,
            qc=qc,
            calc_ts=calc_ts,
            config_hash=config_hash,
            session_open=session_open,
            prior_close=prior_closes.get(key),
            prior_spot=prior_spots.get(key),
            underlying_stale=underlying_stale,
        )

    underlyings = [inst for inst in instruments if not inst.is_option()]
    options = [inst for inst in instruments if inst.is_option()]

    for instrument in underlyings:
        underlying_context = context_for(instrument, underlying_stale=False)
        try:
            item = _build_assessed(instrument, events, context=underlying_context)
        except InsufficientSnapshotData as exc:
            skipped.append(SkippedInstrument(instrument.canonical(), exc.reason))
            continue
        assessed.append(item)
        if "stale_underlying" in item.snapshot.flags:
            stale_underlyings.add(instrument.underlying_symbol)

    for instrument in options:
        underlying_stale = instrument.underlying_symbol in stale_underlyings
        option_context = context_for(instrument, underlying_stale=underlying_stale)
        try:
            item = _build_assessed(instrument, events, context=option_context)
        except InsufficientSnapshotData as exc:
            skipped.append(SkippedInstrument(instrument.canonical(), exc.reason))
            continue
        assessed.append(item)

    return SnapshotBatch(assessed=tuple(assessed), skipped=tuple(skipped))
