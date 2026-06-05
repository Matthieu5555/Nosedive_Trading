"""Universe error type — minimal slice vendored to unblock M5 (ADR 0021).

The full instrument master (``InstrumentUniverse``, ``build_universe``, ``MonitoredUniverse``)
is not yet landed; this module carries only :class:`UniverseError`, which the chain
normaliser (:mod:`algotrading.infra.universe.discovery`) raises, so the broker leaves can
import the slice without pulling the whole master. See ADR 0021 for why this slice exists
and how it is to be reconciled.

NOTE (minimalism follow-up): this :class:`UniverseError` (a ``ValueError``) duplicates the
distinct :class:`algotrading.infra.universe.errors.UniverseError` (an ``Exception``) used by
the rest of the layer — the two should be consolidated.
"""

from __future__ import annotations


class UniverseError(ValueError):
    """Raised on a conflicting or unresolved universe."""
