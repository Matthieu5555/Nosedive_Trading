from __future__ import annotations

from datetime import date

from algotrading.infra.storage import ParquetStore

from .contract import SignalKind
from .signals import SignalReading, SignalSnapshot

_KNOWN_KINDS = frozenset(kind.value for kind in SignalKind)

_SIGNALS_TABLE = "strategy_signals"

_TERM_SLOPE_KIND = SignalKind.TERM_STRUCTURE_SLOPE.value


def signal_snapshot_from_store(
    store: ParquetStore,
    as_of: date,
    *,
    index: str,
    provider: str,
    reference_tenor: str,
) -> SignalSnapshot:
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
