from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from statistics import median

from algotrading.core.config import AnomalyQcConfig, QcThresholdConfig
from algotrading.infra.utils import robust_zscore_vs_baseline

_DEFAULT_ANOMALY = AnomalyQcConfig(version="anomaly-default")


class AnomalyStatus(StrEnum):

    NORMAL = "normal"
    WARN = "warn"
    FAIL = "fail"
    NO_BASELINE = "no_baseline"


@dataclass(frozen=True, slots=True)
class AnomalyThresholds:

    warn_z: float = _DEFAULT_ANOMALY.warn_z
    fail_z: float = _DEFAULT_ANOMALY.fail_z
    min_baseline: int = _DEFAULT_ANOMALY.min_baseline
    threshold_version: str = "anomaly-default"

    def __post_init__(self) -> None:
        if self.fail_z < self.warn_z:
            raise ValueError(f"anomaly fail_z ({self.fail_z}) must be >= warn_z ({self.warn_z})")
        if self.min_baseline < 1:
            raise ValueError(f"anomaly min_baseline ({self.min_baseline}) must be >= 1")


def anomaly_thresholds_from_config(config: QcThresholdConfig) -> AnomalyThresholds:
    anomaly = config.anomaly
    return AnomalyThresholds(
        warn_z=anomaly.warn_z,
        fail_z=anomaly.fail_z,
        min_baseline=anomaly.min_baseline,
        threshold_version=config.version,
    )


@dataclass(frozen=True, slots=True)
class AnomalyOutcome:

    metric: str
    status: AnomalyStatus
    value: float
    robust_z: float | None
    baseline_n: int
    detail: str

    def __post_init__(self) -> None:
        no_baseline = self.status is AnomalyStatus.NO_BASELINE
        if no_baseline and self.robust_z is not None:
            raise ValueError("NO_BASELINE outcome must have robust_z=None")
        if not no_baseline and self.robust_z is None:
            raise ValueError(f"{self.status} outcome must carry a robust_z")


def detect_anomaly(
    metric: str,
    baseline: Sequence[float],
    value: float,
    thresholds: AnomalyThresholds,
) -> AnomalyOutcome:
    if len(baseline) < thresholds.min_baseline:
        return AnomalyOutcome(
            metric=metric,
            status=AnomalyStatus.NO_BASELINE,
            value=value,
            robust_z=None,
            baseline_n=len(baseline),
            detail=f"{len(baseline)} baseline points, need >= {thresholds.min_baseline}",
        )
    z = robust_zscore_vs_baseline(value, baseline)
    magnitude = abs(z)
    if magnitude >= thresholds.fail_z:
        status = AnomalyStatus.FAIL
    elif magnitude >= thresholds.warn_z:
        status = AnomalyStatus.WARN
    else:
        status = AnomalyStatus.NORMAL
    z_text = "inf" if magnitude == float("inf") else f"{z:.2f}"
    detail = f"robust z={z_text} (value={value:g}, baseline median={median(baseline):g})"
    return AnomalyOutcome(metric, status, value, z, len(baseline), detail)


def detect_anomalies(
    baselines: Mapping[str, Sequence[float]],
    current: Mapping[str, float],
    thresholds: AnomalyThresholds,
) -> tuple[AnomalyOutcome, ...]:
    return tuple(
        detect_anomaly(metric, baselines.get(metric, ()), current[metric], thresholds)
        for metric in sorted(current)
    )
