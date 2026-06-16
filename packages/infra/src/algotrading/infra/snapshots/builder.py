from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from algotrading.core.config import QcThresholdConfig
from algotrading.core.provenance import ProvenanceStamp, source_ref, stamp
from algotrading.infra.contracts import InstrumentKey, MarketStateSnapshot, RawMarketEvent

from .as_of import latest_by_field_before
from .quote_quality import QuoteAssessment, assess_quote
from .reference_spot import NoReferenceSpot, resolve_reference_spot

SNAPSHOT_VERSION = "snapshot-1.0.0"
_QUOTE_FIELDS = ("bid", "ask", "last")


class InsufficientSnapshotData(Exception):

    def __init__(self, instrument_key: str, reason: str) -> None:
        self.instrument_key = instrument_key
        self.reason = reason
        super().__init__(f"{instrument_key}: {reason}")


@dataclass(frozen=True, slots=True)
class SnapshotContext:

    snapshot_ts: datetime
    qc: QcThresholdConfig
    calc_ts: datetime
    config_hashes: Mapping[str, str]
    session_open: bool = True
    prior_close: float | None = None
    prior_spot: float | None = None
    underlying_stale: bool = False


@dataclass(frozen=True, slots=True)
class SkippedInstrument:

    instrument_key: str
    reason: str


@dataclass(frozen=True, slots=True)
class AssessedSnapshot:

    snapshot: MarketStateSnapshot
    assessment: QuoteAssessment


@dataclass(frozen=True, slots=True)
class SnapshotBatch:

    assessed: tuple[AssessedSnapshot, ...]
    skipped: tuple[SkippedInstrument, ...]

    @property
    def snapshots(self) -> tuple[MarketStateSnapshot, ...]:
        return tuple(item.snapshot for item in self.assessed)

    @property
    def usable(self) -> tuple[MarketStateSnapshot, ...]:
        return tuple(
            item.snapshot for item in self.assessed if item.assessment.is_usable
        )


def _flags(
    instrument: InstrumentKey, context: SnapshotContext, is_fallback: bool, is_stale: bool
) -> tuple[str, ...]:
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
    refs = tuple(
        source_ref("raw_market_events", event.session_id, event.event_id)
        for event in used_events
    )
    timestamps = tuple(event.canonical_ts for event in used_events)
    return stamp(
        calc_ts=context.calc_ts,
        code_version=SNAPSHOT_VERSION,
        config_hashes=context.config_hashes,
        source_records=refs,
        source_timestamps=timestamps,
    )


def _build_assessed(
    instrument: InstrumentKey,
    events: Sequence[RawMarketEvent],
    *,
    context: SnapshotContext,
) -> AssessedSnapshot:
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
        volume=field_value("volume"),
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
    return _build_assessed(instrument, events, context=context).snapshot


def assess_snapshot(
    instrument: InstrumentKey,
    events: Sequence[RawMarketEvent],
    *,
    context: SnapshotContext,
) -> AssessedSnapshot:
    return _build_assessed(instrument, events, context=context)


def build_snapshots(
    instruments: Sequence[InstrumentKey],
    events: Sequence[RawMarketEvent],
    *,
    snapshot_ts: datetime,
    qc: QcThresholdConfig,
    calc_ts: datetime,
    config_hashes: Mapping[str, str],
    session_open: bool = True,
    prior_closes: Mapping[str, float] | None = None,
    prior_spots: Mapping[str, float] | None = None,
) -> SnapshotBatch:
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
            config_hashes=config_hashes,
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
