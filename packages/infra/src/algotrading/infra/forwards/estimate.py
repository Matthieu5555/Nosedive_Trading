from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime

from algotrading.core.config import ForwardConfig
from algotrading.core.provenance import ProvenanceStamp, snapshot_stamp, source_ref
from algotrading.infra.contracts import ForwardCurvePoint, ForwardDiagnostics
from algotrading.infra.utils.robust import (
    median_absolute_deviation,
    outlier_flags,
    theil_sen_line,
)

from .parity import (
    DegenerateParityFit,
    ParityLine,
    parity_forward_from_pair,
    regress_forward_and_discount_factor,
)

FORWARD_VERSION = "forward-1.0.0"

_MIN_PAIRS_FOR_REGRESSION = 2


_RESIDUAL_REL_FLOOR = 1e-4

REASON_OK = "ok"
REASON_SINGLE_PAIR_FALLBACK = "single_pair_fallback"
REASON_SINGLE_PAIR_NO_DF = "single_pair_no_discount_factor"
REASON_NO_PAIRS = "no_pairs"
REASON_DEGENERATE_FIT = "degenerate_fit"

QUALITY_LABELS = ("good", "fair", "poor")


class ForwardError(Exception):

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class ForwardPair:

    strike: float
    call_mid: float
    put_mid: float
    liquidity: float
    call_key: str
    put_key: str


@dataclass(frozen=True, slots=True)
class StrikePoint:

    strike: float
    parity_spread: float
    weight: float
    residual: float
    rejected: bool
    call_key: str
    put_key: str


@dataclass(frozen=True, slots=True)
class ForwardEstimate:

    underlying: str
    maturity_years: float
    forward: float | None
    discount_factor: float | None
    spot: float | None
    implied_rate: float | None
    implied_carry: float | None
    implied_dividend: float | None
    method: str
    reason_code: str
    quality_label: str
    confidence: float
    candidate_count: int
    used_count: int
    rejected_count: int
    residual_mad: float
    points: tuple[StrikePoint, ...]

    @property
    def is_usable(self) -> bool:
        return (
            self.forward is not None
            and self.discount_factor is not None
            and self.forward > 0.0
            and 0.0 < self.discount_factor <= 1.0
            and self.maturity_years > 0.0
        )


@dataclass(slots=True)
class _Work:

    pair: ForwardPair
    parity_spread: float
    residual: float = 0.0
    rejected: bool = False


def _carry_and_dividend(
    forward: float,
    discount_factor: float,
    spot: float | None,
    maturity_years: float,
    rate: float | None = None,
) -> tuple[float | None, float | None, float | None]:
    effective_rate = rate if rate is not None else -math.log(discount_factor) / maturity_years
    if spot is None or spot <= 0.0:
        return effective_rate, None, None
    implied_carry = math.log(forward / spot) / maturity_years
    implied_dividend = effective_rate - implied_carry
    return effective_rate, implied_carry, implied_dividend


def _quality_and_confidence(
    used_count: int, forward: float, residual_mad: float, *, config: ForwardConfig
) -> tuple[str, float]:
    relative_residual = residual_mad / forward
    if used_count >= 3 and relative_residual <= config.good_rel_residual:
        label = "good"
    elif used_count >= 2 and relative_residual <= config.fair_rel_residual:
        label = "fair"
    else:
        label = "poor"
    count_term = min(1.0, used_count / config.full_credit_pairs)
    fit_term = 1.0 / (1.0 + relative_residual / config.rel_residual_halflife)
    confidence = max(0.0, min(1.0, count_term * fit_term))
    return label, confidence


def _valid_pairs(pairs: tuple[ForwardPair, ...]) -> list[ForwardPair]:
    return [
        pair
        for pair in pairs
        if math.isfinite(pair.call_mid)
        and math.isfinite(pair.put_mid)
        and pair.call_mid >= 0.0
        and pair.put_mid >= 0.0
        and math.isfinite(pair.liquidity)
        and pair.liquidity >= 0.0
    ]


def _no_forward(
    underlying: str,
    maturity_years: float,
    spot: float | None,
    reason_code: str,
    candidate_count: int,
    points: tuple[StrikePoint, ...],
) -> ForwardEstimate:
    return ForwardEstimate(
        underlying=underlying,
        maturity_years=maturity_years,
        forward=None,
        discount_factor=None,
        spot=spot,
        implied_rate=None,
        implied_carry=None,
        implied_dividend=None,
        method="none",
        reason_code=reason_code,
        quality_label="poor",
        confidence=0.0,
        candidate_count=candidate_count,
        used_count=0,
        rejected_count=0,
        residual_mad=0.0,
        points=points,
    )


def estimate_forward(
    underlying: str,
    maturity_years: float,
    pairs: tuple[ForwardPair, ...],
    *,
    config: ForwardConfig,
    spot: float | None = None,
    fallback_discount_factor: float | None = None,
) -> ForwardEstimate:
    valid = _cap_candidates(_valid_pairs(pairs), config.max_candidate_count)
    candidate_count = len(valid)

    if candidate_count == 0:
        return _no_forward(underlying, maturity_years, spot, REASON_NO_PAIRS, 0, ())

    weighted = [pair for pair in valid if pair.liquidity > 0.0]
    distinct_strikes = {pair.strike for pair in weighted}

    if len(distinct_strikes) < _MIN_PAIRS_FOR_REGRESSION:
        return _single_pair(
            underlying, maturity_years, valid, spot, fallback_discount_factor, config=config
        )

    works = [_Work(pair=pair, parity_spread=pair.call_mid - pair.put_mid) for pair in valid]
    if config.outlier_method != "none":
        _flag_outliers(works, rejection_z=config.max_robust_zscore)
    line = _fit_inliers_or_all(works)
    if line is None:
        points = tuple(_point(work) for work in works)
        return _no_forward(
            underlying, maturity_years, spot, REASON_DEGENERATE_FIT, candidate_count, points
        )
    _apply_residuals(works, line)

    forward = line.forward
    discount_factor = line.discount_factor
    kept = [work for work in works if work.pair.liquidity > 0.0 and not work.rejected]
    residual_mad = median_absolute_deviation(tuple(work.residual for work in kept))
    implied_rate, implied_carry, implied_dividend = _carry_and_dividend(
        forward, discount_factor, spot, maturity_years, config.rate
    )
    quality_label, confidence = _quality_and_confidence(
        len(kept), forward, residual_mad, config=config
    )

    return ForwardEstimate(
        underlying=underlying,
        maturity_years=maturity_years,
        forward=forward,
        discount_factor=discount_factor,
        spot=spot,
        implied_rate=implied_rate,
        implied_carry=implied_carry,
        implied_dividend=implied_dividend,
        method="parity_regression",
        reason_code=REASON_OK,
        quality_label=quality_label,
        confidence=confidence,
        candidate_count=candidate_count,
        used_count=len(kept),
        rejected_count=sum(1 for work in works if work.rejected),
        residual_mad=residual_mad,
        points=tuple(_point(work) for work in works),
    )


def _single_pair(
    underlying: str,
    maturity_years: float,
    valid: list[ForwardPair],
    spot: float | None,
    fallback_discount_factor: float | None,
    *,
    config: ForwardConfig,
) -> ForwardEstimate:
    weighted = [pair for pair in valid if pair.liquidity > 0.0]
    pair = weighted[0] if weighted else valid[0]
    points = tuple(
        StrikePoint(
            strike=candidate.strike,
            parity_spread=candidate.call_mid - candidate.put_mid,
            weight=candidate.liquidity,
            residual=0.0,
            rejected=False,
            call_key=candidate.call_key,
            put_key=candidate.put_key,
        )
        for candidate in valid
    )
    usable_df = (
        fallback_discount_factor is not None and 0.0 < fallback_discount_factor <= 1.0
    )
    if not usable_df:
        return _no_forward(
            underlying, maturity_years, spot, REASON_SINGLE_PAIR_NO_DF, len(valid), points
        )
    assert fallback_discount_factor is not None
    forward = parity_forward_from_pair(
        pair.call_mid, pair.put_mid, pair.strike, fallback_discount_factor
    )
    if not (math.isfinite(forward) and forward > 0.0):
        return _no_forward(
            underlying, maturity_years, spot, REASON_SINGLE_PAIR_NO_DF, len(valid), points
        )
    implied_rate, implied_carry, implied_dividend = _carry_and_dividend(
        forward, fallback_discount_factor, spot, maturity_years, config.rate
    )
    return ForwardEstimate(
        underlying=underlying,
        maturity_years=maturity_years,
        forward=forward,
        discount_factor=fallback_discount_factor,
        spot=spot,
        implied_rate=implied_rate,
        implied_carry=implied_carry,
        implied_dividend=implied_dividend,
        method="single_pair_fallback",
        reason_code=REASON_SINGLE_PAIR_FALLBACK,
        quality_label="poor",
        confidence=config.single_pair_confidence,
        candidate_count=len(valid),
        used_count=1,
        rejected_count=0,
        residual_mad=0.0,
        points=points,
    )


def _fit(works: list[_Work]) -> ParityLine:
    active = [work for work in works if work.pair.liquidity > 0.0 and not work.rejected]
    return regress_forward_and_discount_factor(
        tuple(work.pair.strike for work in active),
        tuple(work.parity_spread for work in active),
        tuple(work.pair.liquidity for work in active),
    )


def _apply_residuals(works: list[_Work], line: ParityLine) -> None:
    for work in works:
        work.residual = work.parity_spread - (line.intercept + line.slope * work.pair.strike)


def _cap_candidates(valid: list[ForwardPair], max_count: int | None) -> list[ForwardPair]:
    if max_count is None or len(valid) <= max_count:
        return valid
    order = sorted(range(len(valid)), key=lambda i: (-valid[i].liquidity, valid[i].strike))
    keep = set(order[:max_count])
    return [pair for i, pair in enumerate(valid) if i in keep]


def _flag_outliers(works: list[_Work], *, rejection_z: float) -> None:
    weighted = [work for work in works if work.pair.liquidity > 0.0]
    if len(weighted) < 3:
        return
    try:
        slope, intercept = theil_sen_line(
            tuple(work.pair.strike for work in weighted),
            tuple(work.parity_spread for work in weighted),
        )
    except ValueError:  # pragma: no cover - guaranteed >=2 distinct strikes here
        return
    robust_residuals = tuple(
        work.parity_spread - (intercept + slope * work.pair.strike) for work in weighted
    )
    scale_floor = _RESIDUAL_REL_FLOOR * max(abs(intercept), 1.0)
    flags = outlier_flags(robust_residuals, scale_floor=scale_floor, rejection_z=rejection_z)
    if not any(flags):
        return
    survivors = {work.pair.strike for work, flag in zip(weighted, flags, strict=True) if not flag}
    if len(survivors) < _MIN_PAIRS_FOR_REGRESSION:
        return
    for work, flag in zip(weighted, flags, strict=True):
        if flag:
            work.rejected = True


def _fit_inliers_or_all(works: list[_Work]) -> ParityLine | None:
    try:
        return _fit(works)
    except DegenerateParityFit:
        for work in works:
            work.rejected = False
        try:
            return _fit(works)
        except DegenerateParityFit:
            return None


def _point(work: _Work) -> StrikePoint:
    return StrikePoint(
        strike=work.pair.strike,
        parity_spread=work.parity_spread,
        weight=work.pair.liquidity,
        residual=work.residual,
        rejected=work.rejected,
        call_key=work.pair.call_key,
        put_key=work.pair.put_key,
    )


def forward_curve_point(
    estimate: ForwardEstimate,
    *,
    snapshot_ts: datetime,
    expiry_date: date,
    day_count: str,
    source_snapshot_ts: datetime,
    calc_ts: datetime,
    config_hashes: Mapping[str, str],
) -> ForwardCurvePoint:
    if not estimate.is_usable:
        raise ForwardError(
            f"estimate for {estimate.underlying} is not usable ({estimate.reason_code})"
        )
    assert estimate.forward is not None

    used = [point for point in estimate.points if point.weight > 0.0 and not point.rejected]
    refs = []
    for point in used:
        refs.append(source_ref("market_state_snapshots", source_snapshot_ts, point.call_key))
        refs.append(source_ref("market_state_snapshots", source_snapshot_ts, point.put_key))
    provenance: ProvenanceStamp = snapshot_stamp(
        calc_ts=calc_ts,
        code_version=FORWARD_VERSION,
        config_hashes=config_hashes,
        source_snapshot_ts=source_snapshot_ts,
        source_records=tuple(refs),
    )
    diagnostics = ForwardDiagnostics(
        method=estimate.method,
        candidate_count=estimate.candidate_count,
        residual_mad=estimate.residual_mad,
        quality_label=estimate.quality_label,
    )
    return ForwardCurvePoint(
        snapshot_ts=snapshot_ts,
        underlying=estimate.underlying,
        maturity_years=estimate.maturity_years,
        expiry_date=expiry_date,
        day_count=day_count,
        forward_price=estimate.forward,
        diagnostics=diagnostics,
        source_snapshot_ts=source_snapshot_ts,
        provenance=provenance,
    )
