"""Assemble a :class:`SignalSnapshot` from the persisted infra signal layer — S1's live feed.

The strategy reads a :class:`~algotrading.strategy.signals.SignalSnapshot`; the infra signal
layer (``algotrading.infra.signals``) persists the readings as ``strategy_signals`` rows. This
is the as-of seam between them: read one day's signal partition for an index and build the
snapshot the strategy harness injects — the same object research/backtest/paper/live all hand
to the strategy, now sourced from the real persisted signals rather than a hand-built fixture.

It lives in the strategy layer (not infra) because it depends on both sides: the infra store
and contract below, and the strategy ``SignalSnapshot``/``SignalKind`` it produces. Infra stays
blind to alpha; this adapter is the one place the two meet.
"""

from __future__ import annotations

from datetime import date

from algotrading.infra.storage import ParquetStore

from .contract import SignalKind
from .signals import SignalReading, SignalSnapshot

# The persisted ``signal_kind`` strings are the ``SignalKind`` enum values (infra mirrors them
# as constants, blind to this enum). ``SignalKind(value)`` is the inverse map; an unrecognised
# kind is skipped rather than crashing the read, so a newer infra signal does not break an
# older strategy.
_KNOWN_KINDS = frozenset(kind.value for kind in SignalKind)

_SIGNALS_TABLE = "strategy_signals"

# ρ̄ and the per-name range signals are persisted per tenor; the snapshot surfaces the strategy's
# reference tenor so each (kind, subject) yields exactly one reading and ``latest`` is unambiguous.
# A term-structure slope is keyed by a ``front:back`` pillar pair, not the reference tenor, so it
# is always surfaced. The full per-tenor set stays in the store for a caller that wants it.
_TERM_SLOPE_KIND = SignalKind.TERM_STRUCTURE_SLOPE.value


def signal_snapshot_from_store(
    store: ParquetStore,
    as_of: date,
    *,
    index: str,
    provider: str,
    reference_tenor: str,
) -> SignalSnapshot:
    """Build the as-of :class:`SignalSnapshot` for ``index`` from the persisted signal layer.

    Reads the live ``strategy_signals`` partition for ``(as_of, index, provider)`` and turns
    each reading at ``reference_tenor`` (plus every term-structure slope) into a
    :class:`SignalReading`, preserving its ``subject`` (the index for ρ̄, a constituent for a
    per-name reading). An index/day with no persisted signals yields an empty snapshot — a
    labelled absence the strategy holds on, never a fabricated reading.
    """
    rows = store.read(_SIGNALS_TABLE, trade_date=as_of, underlying=index, provider=provider)
    readings: list[SignalReading] = []
    for row in rows:
        if row.signal_kind not in _KNOWN_KINDS:
            continue
        if row.signal_kind != _TERM_SLOPE_KIND and row.tenor_label != reference_tenor:
            continue
        readings.append(
            SignalReading(
                kind=SignalKind(row.signal_kind),
                value=row.value,
                subject=row.subject,
            )
        )
    return SignalSnapshot(as_of=as_of, readings=tuple(readings))
