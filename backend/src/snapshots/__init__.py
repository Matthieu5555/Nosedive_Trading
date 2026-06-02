"""Snapshot builder — raw events in, a quality-labeled MarketStateSnapshot out.

The pure heart of step 5. ``latest_by_field_before`` is the as-of read (the
look-ahead boundary); ``resolve_reference_spot`` chooses a labeled reference price
with documented fallbacks; ``assess_quote`` runs the named quote-QC checks; and
``build_snapshot``/``build_snapshots`` assemble the stamped contract. Quote QC is
wired into the build path: ``assess_snapshot`` returns a snapshot with its verdict,
and ``build_snapshots`` returns a :class:`SnapshotBatch` carrying both the full set
and the QC-filtered ``usable`` view (step 7), so a rejected quote is excluded from
what feeds forward/IV yet stays queryable with its reasons.

    from snapshots import build_snapshot, SnapshotContext, latest_by_field_before
"""

from __future__ import annotations

from .as_of import latest_by_field_before
from .builder import (
    SNAPSHOT_VERSION,
    AssessedSnapshot,
    InsufficientSnapshotData,
    SkippedInstrument,
    SnapshotBatch,
    SnapshotContext,
    assess_snapshot,
    build_snapshot,
    build_snapshots,
)
from .quote_quality import (
    QUOTE_STATUSES,
    QuoteAssessment,
    assess_quote,
    check_bid_positive,
    check_crossed_or_locked,
    check_open_interest,
    check_price_against_intrinsic,
    check_quote_age,
    check_spread,
    cross_strike_monotonicity_violations,
)
from .reference_spot import (
    REFERENCE_TYPES,
    NoReferenceSpot,
    ReferenceSpot,
    is_valid_two_sided,
    resolve_reference_spot,
)

__all__ = [
    "QUOTE_STATUSES",
    "REFERENCE_TYPES",
    "SNAPSHOT_VERSION",
    "AssessedSnapshot",
    "InsufficientSnapshotData",
    "NoReferenceSpot",
    "QuoteAssessment",
    "ReferenceSpot",
    "SkippedInstrument",
    "SnapshotBatch",
    "SnapshotContext",
    "assess_quote",
    "assess_snapshot",
    "build_snapshot",
    "build_snapshots",
    "check_bid_positive",
    "check_crossed_or_locked",
    "check_open_interest",
    "check_price_against_intrinsic",
    "check_quote_age",
    "check_spread",
    "cross_strike_monotonicity_violations",
    "is_valid_two_sided",
    "latest_by_field_before",
    "resolve_reference_spot",
]
