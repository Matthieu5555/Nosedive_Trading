"""Anomaly detection against a rolling baseline.

A run can pass every static QC check and still be *abnormal* relative to its own recent
history — a usable-quote count that quietly collapses, a fit error that creeps up day
over day. This scores a current metric against a rolling window of prior values with a
robust (median/MAD) z-score, so a few outliers in the baseline cannot inflate the scale
and hide a real shift. Too little history is reported as ``NO_BASELINE`` — never silently
treated as normal, which is the failure mode that lets a cold-start run look healthy.

This is the depth the static QC plane does not have. The robust z-score here is a
self-contained primitive (no dependency on the QC plane), deliberately mirroring the one
in :func:`algotrading.infra.qc.robust_z_score`; both compute the same median/MAD
statistic, and each plane's test pins it against an independent hand computation. A future
shared ``utils`` could unify them, but the validation plane stays importable on its own
until then.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from algotrading.core.config import AnomalyQcConfig, QcThresholdConfig

# The anomaly bands are economic and live in the hashed ``qc`` config block
# (``AnomalyQcConfig``), never as ``.py`` literals (ADR 0028). The dataclass field defaults
# below take their values from that schema's defaults — the single source of truth — so a
# no-argument ``AnomalyThresholds()`` (the in-memory/test construction) matches what the
# config-default block would hydrate, while a production run hydrates from the loaded,
# hashed ``qc.yaml`` via :func:`anomaly_thresholds_from_config`.
_DEFAULT_ANOMALY = AnomalyQcConfig(version="anomaly-default")


def _median(values: Sequence[float]) -> float:
    """The median of a non-empty sequence (no external dependency)."""
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return 0.5 * (ordered[mid - 1] + ordered[mid])


def robust_zscore_vs_baseline(value: float, baseline: Sequence[float]) -> float:
    """A signed median/MAD robust z-score of ``value`` against a rolling ``baseline``.

    Uses the median absolute deviation rather than the standard deviation so a single
    earlier spike in the baseline does not inflate the scale and mask a new one. When the
    baseline has no spread (all values equal), a value on the median scores 0 and any
    departure scores +/- infinity — the only honest answers for a degenerate scale. The
    sign is kept (positive above the median, negative below) so the detail line reads
    which way the metric moved; the bands compare on magnitude. The baseline must be
    non-empty (the caller gates on ``min_baseline`` first).
    """
    center = _median(baseline)
    deviations = [abs(v - center) for v in baseline]
    mad = _median(deviations)
    if mad == 0.0:
        if value == center:
            return 0.0
        return float("inf") if value > center else float("-inf")
    # 1.4826 scales MAD to a standard-deviation-equivalent for normal data.
    return (value - center) / (1.4826 * mad)


class AnomalyStatus(StrEnum):
    """The outcome of scoring one metric against its baseline."""

    NORMAL = "normal"
    WARN = "warn"
    FAIL = "fail"
    # Too little history to judge — reported as its own state, never assumed normal.
    NO_BASELINE = "no_baseline"


@dataclass(frozen=True, slots=True)
class AnomalyThresholds:
    """The robust-z bands and the minimum baseline length to judge an anomaly.

    Modelled on the QC plane's threshold bundle. Every field is economic and its default is
    sourced from the hashed ``qc`` config block (:class:`AnomalyQcConfig`) — not a ``.py``
    literal — so a no-argument construction matches what the config-default block hydrates
    (ADR 0028); a production run hydrates from the loaded, hashed config via
    :func:`anomaly_thresholds_from_config`. ``threshold_version`` makes every outcome
    traceable to the config that judged it. The ordering invariants (``fail_z >= warn_z``,
    ``min_baseline >= 1``) are enforced here so a mis-tuned config fails loudly at
    construction, not silently at runtime.
    """

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
    """Hydrate :class:`AnomalyThresholds` from the hashed ``qc`` config's anomaly block.

    The bands come from ``config.anomaly`` (an :class:`AnomalyQcConfig` folded into
    ``config_hashes["qc"]``), so the values that judge a run are the same ones the
    reproducibility hash covers (ADR 0028). ``threshold_version`` carries the ``qc`` section
    version, so every outcome points back at the economics version that judged it.
    """
    anomaly = config.anomaly
    return AnomalyThresholds(
        warn_z=anomaly.warn_z,
        fail_z=anomaly.fail_z,
        min_baseline=anomaly.min_baseline,
        threshold_version=config.version,
    )


@dataclass(frozen=True, slots=True)
class AnomalyOutcome:
    """One metric scored against its rolling baseline.

    ``robust_z`` is the signed score, and is ``None`` exactly when the status is
    ``NO_BASELINE`` (there was nothing to score against). That coupling is enforced so a
    ``NO_BASELINE`` outcome can never carry a meaningless number, nor a judged outcome a
    missing one.
    """

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
    """Score one metric's ``value`` against its rolling ``baseline``.

    Returns ``NO_BASELINE`` when there is too little history to judge (the count is
    reported, not assumed normal). Otherwise the magnitude of the robust z-score is
    banded: ``>= fail_z`` is a FAIL, ``>= warn_z`` is a WARN, below is NORMAL.
    """
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
    detail = f"robust z={z_text} (value={value:g}, baseline median={_median(baseline):g})"
    return AnomalyOutcome(metric, status, value, z, len(baseline), detail)


def detect_anomalies(
    baselines: Mapping[str, Sequence[float]],
    current: Mapping[str, float],
    thresholds: AnomalyThresholds,
) -> tuple[AnomalyOutcome, ...]:
    """Score every current metric against its baseline, in sorted metric order.

    The output order is the sorted metric names, so two runs over the same metrics
    produce an identically ordered tuple regardless of mapping insertion order — the
    determinism a stored/replayed result depends on. A metric with no baseline entry is
    scored against an empty baseline, which yields ``NO_BASELINE``.
    """
    return tuple(
        detect_anomaly(metric, baselines.get(metric, ()), current[metric], thresholds)
        for metric in sorted(current)
    )
