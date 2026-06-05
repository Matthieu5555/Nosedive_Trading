"""Historical reconstruction and replay over a date range (roadmap step 13).

Reconstruction is :func:`actor.run_day` run over a range of stored trade dates — the
*same* compute path as live, never a second engine that could drift (ADR 0007,
decision 4). This subpackage adds only the batch layer on top of that one function:

* :func:`reconstruct_range` / :func:`reconstruct_day` — replay each stored day in
  ``[start, end]`` in order, returning a :class:`ReconstructionReport` of what ran,
  what was skipped, and why. A day with no stored raw partition is flagged
  :data:`MISSING` and produces no output — never a fabricated empty result.
* Versioned restatement — pass ``version=<V>`` so a newer-code run writes each derived
  table into its own ``version=<V>`` sub-partition, leaving the older analytic intact
  beside it (A's storage versioning; ADR 0007, decision 3).
* :func:`compare_replay_to_live` — for a day already run live, compare a
  reconstruction's outputs to the persisted live rows per table; under one code
  version they must agree, and this names the divergence if they ever stop.

    from algotrading.infra.orchestration.reconstruction import (
        reconstruct_range, compare_replay_to_live,
    )
"""

from __future__ import annotations

from .batch import (
    reconstruct_day,
    reconstruct_range,
    stored_trade_dates,
)
from .comparison import compare_replay_to_live
from .report import (
    EMPTY,
    MISSING,
    RECONSTRUCTED,
    DayReconstruction,
    ReconstructionReport,
    ReplayComparison,
    TableAgreement,
)

__all__ = [
    "EMPTY",
    "MISSING",
    "RECONSTRUCTED",
    "DayReconstruction",
    "ReconstructionReport",
    "ReplayComparison",
    "TableAgreement",
    "compare_replay_to_live",
    "reconstruct_day",
    "reconstruct_range",
    "stored_trade_dates",
]
