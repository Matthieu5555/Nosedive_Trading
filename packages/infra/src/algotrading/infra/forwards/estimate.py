"""Estimate one maturity's forward and discount factor from a chain of pairs.

This is the orchestration of step 6: take the liquid near-the-money call/put pairs
for one underlying and maturity, recover the forward ``F`` and discount factor
``DF`` jointly from the put-call-parity line (:mod:`forwards.parity`), reject
outlier strikes by MAD, derive the implied carry and dividend, and score the whole
thing with a confidence and a reason code. It is a pure function of its inputs: no
I/O, no clock, no randomness; the ``calc_ts`` for the stamp is injected.

The result is a rich in-memory :class:`ForwardEstimate` — deliberately more detailed
than A's :class:`~contracts.ForwardDiagnostics`, which is a flat summary. The
estimate keeps the discount factor, the implied carry/dividend, and the per-strike
weights and residuals, because the IV solver downstream needs ``(F, DF)`` and a
human debugging a bad maturity needs to see which strikes were rejected and why.
:func:`forward_curve_point` projects the usable part into A's persisted contract.

Why a rich result instead of just the contract: ``ForwardCurvePoint`` persists only
the forward, but the IV solver needs the discount factor too (it inverts prices that
were discounted by ``DF``). Threading the in-memory estimate from forwards to IV
keeps that coupling typed and explicit rather than re-deriving ``DF`` downstream.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime

from algotrading.core.config import ForwardConfig
from algotrading.core.provenance import ProvenanceStamp, source_ref, stamp
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

# Bump only on a real change to the forward logic, never on config.
FORWARD_VERSION = "forward-1.0.0"

# A regression identifies two unknowns (F and DF), so it needs at least two pairs. A
# mathematical invariant (two equations for two unknowns), not a tunable — stays code.
_MIN_PAIRS_FOR_REGRESSION = 2

# The confidence/quality heuristics that shape every consumer's trust in a maturity now
# live in ForwardConfig (pricing.yaml under forward:); they are economic inputs, not code
# constants. relative_residual == residual_mad / forward is dimensionless.

# Outlier-scale floor as a fraction of the price level (|intercept| == DF*F): a parity
# residual smaller than this is quote rounding, not an outlier. It stops the MAD scale
# from collapsing to float noise on a near-perfect chain (which would flag clean
# strikes); on genuinely noisy chains real residuals exceed it and it does not bind.
_RESIDUAL_REL_FLOOR = 1e-4

# Reason codes, every terminal state labeled so a flagged maturity is queryable.
REASON_OK = "ok"
REASON_SINGLE_PAIR_FALLBACK = "single_pair_fallback"
REASON_SINGLE_PAIR_NO_DF = "single_pair_no_discount_factor"
REASON_NO_PAIRS = "no_pairs"
REASON_DEGENERATE_FIT = "degenerate_fit"

QUALITY_LABELS = ("good", "fair", "poor")


class ForwardError(Exception):
    """A usable forward was requested from an estimate that does not have one."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class ForwardPair:
    """One call/put pair at a strike, with a liquidity weight and lineage keys.

    ``liquidity`` is a non-negative caller-supplied weight proxy (e.g. inverse
    spread, quoted size, or open interest); a zero weight drops the strike from the
    fit entirely (Eq 4). ``call_key``/``put_key`` are the canonical instrument keys
    of the two contracts, carried so the emitted forward's provenance can name the
    exact snapshots it was built from.
    """

    strike: float
    call_mid: float
    put_mid: float
    liquidity: float
    call_key: str
    put_key: str


@dataclass(frozen=True, slots=True)
class StrikePoint:
    """One strike's contribution to the fit: its parity spread, weight, residual.

    ``rejected`` is True when MAD outlier rejection dropped this strike; a rejected
    strike keeps its residual (which is why it was dropped) so the rejection is
    auditable, never silent.
    """

    strike: float
    parity_spread: float  # call_mid - put_mid
    weight: float
    residual: float
    rejected: bool
    call_key: str
    put_key: str


@dataclass(frozen=True, slots=True)
class ForwardEstimate:
    """The full, rich result of estimating one maturity's forward.

    ``forward`` and ``discount_factor`` are ``None`` exactly when no honest estimate
    could be built (no pairs, an unidentified single pair, or a degenerate fit); in
    that case ``reason_code`` says which, and :attr:`is_usable` is False. When usable,
    ``implied_carry`` (``b = ln(F/spot)/T``) and ``implied_dividend`` (``q = r - b``)
    are filled when a positive ``spot`` was supplied.
    """

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
        """True when a positive forward and a valid discount factor were recovered."""
        return (
            self.forward is not None
            and self.discount_factor is not None
            and self.forward > 0.0
            and 0.0 < self.discount_factor <= 1.0
            and self.maturity_years > 0.0
        )


@dataclass(slots=True)
class _Work:
    """Mutable scratch for one strike while fitting (residual/rejected change)."""

    pair: ForwardPair
    parity_spread: float
    residual: float = 0.0
    rejected: bool = False


def _carry_and_dividend(
    forward: float, discount_factor: float, spot: float | None, maturity_years: float
) -> tuple[float | None, float | None, float | None]:
    """Implied rate, cost-of-carry, and dividend yield (Eq 5).

    ``r = -ln(DF)/T``; ``b = ln(F/spot)/T``; ``q = r - b``. Carry and dividend need a
    positive spot, so they are ``None`` without one (the rate only needs ``DF``).
    """
    implied_rate = -math.log(discount_factor) / maturity_years
    if spot is None or spot <= 0.0:
        return implied_rate, None, None
    implied_carry = math.log(forward / spot) / maturity_years
    implied_dividend = implied_rate - implied_carry
    return implied_rate, implied_carry, implied_dividend


def _quality_and_confidence(
    used_count: int, forward: float, residual_mad: float, *, config: ForwardConfig
) -> tuple[str, float]:
    """Map used-pair count and relative fit residual to a label and a 0..1 score.

    The caller only reaches this with a positive forward (the regression enforces
    ``forward > 0``), so the relative residual is always well-defined.
    """
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
    """Keep pairs whose mids and weight are finite and non-negative."""
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
    """Build a labeled, low-confidence estimate that carries no usable forward."""
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
    """Estimate the forward and discount factor for one underlying and maturity.

    Pure and total: every terminal state (a clean fit, a single-pair fallback, no
    pairs, or a degenerate fit) returns a labeled :class:`ForwardEstimate`, never a
    raise. With two or more positively-weighted strikes it fits the parity line,
    rejects MAD outliers, and refits; with a single pair it falls back to
    ``fallback_discount_factor`` if one is given; with none it reports the reason.
    """
    valid = _valid_pairs(pairs)
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
    _flag_outliers(works)
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
        forward, discount_factor, spot, maturity_years
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
    """Handle the one-identifiable-strike case: fall back on a supplied DF, or label."""
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
    assert fallback_discount_factor is not None  # narrowed by usable_df
    forward = parity_forward_from_pair(
        pair.call_mid, pair.put_mid, pair.strike, fallback_discount_factor
    )
    if not (math.isfinite(forward) and forward > 0.0):
        return _no_forward(
            underlying, maturity_years, spot, REASON_SINGLE_PAIR_NO_DF, len(valid), points
        )
    implied_rate, implied_carry, implied_dividend = _carry_and_dividend(
        forward, fallback_discount_factor, spot, maturity_years
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
    """Run the weighted parity regression over the current (non-rejected) points."""
    active = [work for work in works if work.pair.liquidity > 0.0 and not work.rejected]
    return regress_forward_and_discount_factor(
        tuple(work.pair.strike for work in active),
        tuple(work.parity_spread for work in active),
        tuple(work.pair.liquidity for work in active),
    )


def _apply_residuals(works: list[_Work], line: ParityLine) -> None:
    """Set every point's residual against the fitted line (rejected ones included)."""
    for work in works:
        work.residual = work.parity_spread - (line.intercept + line.slope * work.pair.strike)


def _flag_outliers(works: list[_Work]) -> None:
    """Mark MAD outliers (Eq 24), detected off a robust Theil-Sen line.

    Detection uses Theil-Sen residuals, not least-squares residuals, so a
    high-leverage wing strike cannot mask itself by dragging the fitting line onto
    it. Rejection is skipped when fewer than three weighted points exist (too few to
    estimate spread) or when it would leave fewer than two distinct strikes (which
    would starve the downstream regression).
    """
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
    flags = outlier_flags(robust_residuals, scale_floor=scale_floor)
    if not any(flags):
        return
    survivors = {work.pair.strike for work, flag in zip(weighted, flags, strict=True) if not flag}
    if len(survivors) < _MIN_PAIRS_FOR_REGRESSION:
        return
    for work, flag in zip(weighted, flags, strict=True):
        if flag:
            work.rejected = True


def _fit_inliers_or_all(works: list[_Work]) -> ParityLine | None:
    """Fit the inliers; if that is degenerate, unreject and retry; else give up.

    Returns ``None`` only when even the full set cannot produce a physical forward
    and discount factor, which the caller reports as a degenerate fit.
    """
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
    """Freeze one working point into its public :class:`StrikePoint`."""
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
    config_hash: str,
) -> ForwardCurvePoint:
    """Project a usable estimate into A's stamped ``ForwardCurvePoint`` contract.

    Raises :class:`ForwardError` if the estimate carries no usable forward — a
    rejected/low-confidence maturity is never silently emitted as a forward. The
    provenance stamp names every used strike's call and put snapshot as a source,
    so lineage resolves to the exact rows that fed the fit.
    """
    if not estimate.is_usable:
        raise ForwardError(
            f"estimate for {estimate.underlying} is not usable ({estimate.reason_code})"
        )
    assert estimate.forward is not None  # narrowed by is_usable

    used = [point for point in estimate.points if point.weight > 0.0 and not point.rejected]
    refs = []
    for point in used:
        refs.append(source_ref("market_state_snapshots", source_snapshot_ts, point.call_key))
        refs.append(source_ref("market_state_snapshots", source_snapshot_ts, point.put_key))
    provenance: ProvenanceStamp = stamp(
        calc_ts=calc_ts,
        code_version=FORWARD_VERSION,
        config_hash=config_hash,
        source_records=tuple(refs),
        source_timestamps=tuple(source_snapshot_ts for _ in refs),
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
        forward=estimate.forward,
        diagnostics=diagnostics,
        source_snapshot_ts=source_snapshot_ts,
        provenance=provenance,
    )
