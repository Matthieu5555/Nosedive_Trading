"""Universe error type — minimal slice vendored to unblock M5 (ADR 0021).

The full instrument master (``InstrumentUniverse``, ``build_universe``, ``MonitoredUniverse``)
is M4's to land when the market-data plane relocates from ``backend/`` into
``packages/infra``. This module carries only :class:`UniverseError`, which the chain
normaliser (:mod:`algotrading.infra.universe.discovery`) raises, so the broker leaves can
import the slice without pulling the whole master. See ADR 0021 for why this slice exists
ahead of M4 and how it is to be reconciled.
"""

from __future__ import annotations


class UniverseError(ValueError):
    """Raised on a conflicting or unresolved universe."""
