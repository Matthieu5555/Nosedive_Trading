from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from algotrading.core.log import get_logger

from .greeks import PositionRisk

RECON_TOLERANCE_VERSION = "risk-recon-1.0.0"

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BrokerGreeks:

    contract_key: str
    delta: float | None = None
    gamma: float | None = None
    vega: float | None = None
    theta: float | None = None


@dataclass(frozen=True, slots=True)
class ReconciliationTolerance:

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
