"""Reconcile computed Greeks against broker-returned Greeks; surface the breaches.

Step 11 requires that discrepancies beyond a threshold are surfaced automatically, and
the blueprint is explicit that this is diagnostic only (``risk/aggregation.py``:
"Reconcile to broker Greeks only as diagnostics, never as the source of truth"). The
broker may return only some Greeks, so each is optional and a missing one is skipped
(not treated as zero — an absent broker value is not a disagreement). The thresholds are
versioned so "what counts as a breach" is part of the data lineage, and only the breaches
are returned: a quiet, empty result means everything agreed.

A non-finite broker Greek (NaN/inf) is treated as a breach, not silent agreement: a bare
``abs_diff > threshold`` would read ``nan`` as "agrees". This guard is correctness, not
taste, and it goes in.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from algotrading.core.log import get_logger

from .greeks import PositionRisk

# Per-unit absolute thresholds. Versioned so a change to "what is a breach" is a
# deliberate, reviewable bump, not a silent edit.
RECON_TOLERANCE_VERSION = "risk-recon-1.0.0"

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BrokerGreeks:
    """Per-unit Greeks as returned by the broker. Any field may be ``None`` (absent)."""

    contract_key: str
    delta: float | None = None
    gamma: float | None = None
    vega: float | None = None
    theta: float | None = None


@dataclass(frozen=True, slots=True)
class ReconciliationTolerance:
    """Per-Greek absolute thresholds beyond which a difference is a breach."""

    version: str
    delta: float
    gamma: float
    vega: float
    theta: float


DEFAULT_RECON_TOLERANCE = ReconciliationTolerance(
    version=RECON_TOLERANCE_VERSION, delta=1e-3, gamma=1e-3, vega=1e-2, theta=1e-2
)


@dataclass(frozen=True, slots=True)
class GreekDiscrepancy:
    """One Greek whose computed-vs-broker difference exceeded the threshold."""

    contract_key: str
    greek: str
    computed: float
    broker: float
    abs_diff: float
    threshold: float
    threshold_version: str


def reconcile(
    line: PositionRisk,
    broker: BrokerGreeks,
    *,
    tolerance: ReconciliationTolerance = DEFAULT_RECON_TOLERANCE,
) -> list[GreekDiscrepancy]:
    """Return the Greeks whose per-unit computed value differs from broker beyond threshold.

    Compares only the Greeks the broker actually returned. The result is empty when
    everything is within tolerance; each entry is one surfaced breach, ordered
    delta, gamma, vega, theta.
    """
    computed = line.greeks
    pairs = (
        ("delta", computed.delta, broker.delta, tolerance.delta),
        ("gamma", computed.gamma, broker.gamma, tolerance.gamma),
        ("vega", computed.vega, broker.vega, tolerance.vega),
        ("theta", computed.theta, broker.theta, tolerance.theta),
    )
    breaches: list[GreekDiscrepancy] = []
    for name, mine, theirs, threshold in pairs:
        if theirs is None:
            continue
        abs_diff = abs(mine - theirs)
        # A non-finite broker value (NaN/inf) is corrupt data, not agreement: surface
        # it. ``nan > threshold`` is False, so without this a NaN would read as "agrees".
        if not math.isfinite(theirs) or abs_diff > threshold:
            breaches.append(
                GreekDiscrepancy(
                    contract_key=line.contract_key,
                    greek=name,
                    computed=mine,
                    broker=theirs,
                    abs_diff=abs_diff,
                    threshold=threshold,
                    threshold_version=tolerance.version,
                )
            )
    return breaches


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """Outcome of a reconciliation pass over a book: the breaches and the compare count.

    ``compared`` is the number of (contract, greek) pairs actually compared — a breach
    rate needs the denominator. ``ok`` is True when no breach surfaced.
    """

    breaches: tuple[GreekDiscrepancy, ...]
    compared: int
    threshold_version: str

    @property
    def ok(self) -> bool:
        return not self.breaches


def reconcile_report(
    lines: Iterable[PositionRisk],
    broker_by_contract: Mapping[str, BrokerGreeks],
    *,
    tolerance: ReconciliationTolerance = DEFAULT_RECON_TOLERANCE,
) -> ReconciliationReport:
    """Reconcile every line that has a broker counterpart, collect the breaches, and log.

    Only contracts present in both sides and Greeks the broker actually supplied are
    compared. Breaches surface automatically via a warning log rather than hiding in a
    column nobody reads, matching the blueprint's "so they surface automatically".
    Lines are processed in contract-key order so the report is deterministic.
    """
    breaches: list[GreekDiscrepancy] = []
    compared = 0
    for line in sorted(lines, key=lambda ln: ln.contract_key):
        broker = broker_by_contract.get(line.contract_key)
        if broker is None:
            continue
        compared += sum(
            1
            for value in (broker.delta, broker.gamma, broker.vega, broker.theta)
            if value is not None
        )
        breaches.extend(reconcile(line, broker, tolerance=tolerance))
    if breaches:
        _log.warning(
            "greek reconciliation breaches: %d breach(es) over %d compared pair(s)",
            len(breaches),
            compared,
            extra={"n_breaches": len(breaches), "compared": compared},
        )
    return ReconciliationReport(
        breaches=tuple(breaches), compared=compared, threshold_version=tolerance.version
    )
