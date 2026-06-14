"""The entry-signal input type a :class:`~algotrading.strategy.Strategy` reads.

The strategy *reads* the signal layer's outputs (ρ̄ / IV-rank / RV−IV / term-slope, TARGET
§3); it does not compute them — that is the infra signal layer's job (``infra-signal-layer``,
a separate lane). This module defines the **type** of that input so the protocol is buildable
now and the decisions go live unchanged when the signal lane lands: today this is the agreed
shape; when infra publishes its signal contract, ``SignalSnapshot`` becomes a thin re-export
or alias of it (the strategy code that consumes it does not change).

It is deliberately a sparse, as-of-stamped bag of named scalar readings, not a fixed record
with one field per signal: different strategies read different signals (S1 reads ρ̄, S3 reads
IV-rank), and a strategy asks for the reading it needs by :class:`~.contract.SignalKind`.
A reading that is absent is a labelled absence (``None`` from the lookup), never a silent
zero — a strategy that needs a missing signal must hold, not act on a fabricated 0.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date

from .contract import SignalKind


@dataclass(frozen=True, slots=True)
class SignalReading:
    """One named signal value the infra signal layer published, with its subject.

    ``value`` is the scalar reading (ρ̄, an IV-rank in [0, 1], an RV−IV spread, a term
    slope). ``subject`` scopes *what* it was read on — an index name, a single-name ticker,
    a tenor pair — so a per-name strategy (S3 reads IV-rank *per name*) can distinguish
    readings; ``None`` for an index-level scalar (S1's book-wide ρ̄). The value is the signal
    layer's output verbatim; the strategy interprets it, the signal layer derives it.
    """

    kind: SignalKind
    value: float
    subject: str | None = None


@dataclass(frozen=True, slots=True)
class SignalSnapshot:
    """The as-of-stamped set of signal readings a strategy reads at one decision point.

    ``as_of`` is the look-ahead anchor: the snapshot carries only readings the signal layer
    could compute *as of that date* — a strategy decision is a pure function of it and never
    reaches past it (the §6 no-look-ahead bar). ``readings`` is the published set; the lookup
    helpers return a labelled absence (``None`` / empty tuple) for a signal not in the
    snapshot, never a fabricated value.

    This is the input type the §6 four-context harness injects: research, backtest, paper,
    and live each build a ``SignalSnapshot`` from their own data source and hand it to the
    *same* strategy object, which reads it identically in all four.
    """

    as_of: date
    readings: tuple[SignalReading, ...] = field(default_factory=tuple)

    def latest(self, kind: SignalKind, *, subject: str | None = None) -> SignalReading | None:
        """The reading for ``kind`` (and ``subject`` if given), or ``None`` if absent.

        A labelled absence, not a fabricated zero: a strategy that needs a missing signal
        holds rather than acting on an invented value.
        """
        for reading in self.readings:
            if reading.kind == kind and reading.subject == subject:
                return reading
        return None

    def all_of(self, kind: SignalKind) -> tuple[SignalReading, ...]:
        """Every reading of ``kind`` across subjects (e.g. per-name IV-rank for a basket)."""
        return tuple(reading for reading in self.readings if reading.kind == kind)


def signal_snapshot(as_of: date, readings: Mapping[SignalKind, float]) -> SignalSnapshot:
    """Build an index-level :class:`SignalSnapshot` from a kind→value map (no subjects).

    The common case for an index-level strategy: one scalar reading per signal kind, all on
    the same (index) subject. Per-name snapshots build :class:`SignalReading` rows directly.
    """
    return SignalSnapshot(
        as_of=as_of,
        readings=tuple(SignalReading(kind=kind, value=value) for kind, value in readings.items()),
    )
