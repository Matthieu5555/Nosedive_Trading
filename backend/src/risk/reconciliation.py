"""Reconcile computed Greeks against broker-returned Greeks; surface the breaches.

Step 11 requires that discrepancies beyond a threshold are surfaced automatically.
The broker may return only some Greeks, so each is optional and a missing one is
skipped (not treated as zero — an absent broker value is not a disagreement). The
thresholds are versioned so "what counts as a breach" is part of the data lineage,
and only the breaches are returned: a quiet, empty result means everything agreed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .greeks import PositionRisk

# Per-unit absolute thresholds. Versioned so a change to "what is a breach" is a
# deliberate, reviewable bump, not a silent edit.
RECON_TOLERANCE_VERSION = "risk-recon-1.0.0"


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
