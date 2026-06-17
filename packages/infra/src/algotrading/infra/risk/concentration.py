from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .aggregation import GROUP_DIMENSIONS, AggregationError, aggregate_by_key
from .greeks import PositionRisk

CONCENTRATION_VERSION = "concentration-1.0.0"

# The four signed sensitivities a net-exposure bucket carries (aggregation.py).
GREEK_AXES = ("delta", "gamma", "vega", "theta")


class ConcentrationError(Exception):
    pass


def _validate_greek(greek: str) -> None:
    if greek not in GREEK_AXES:
        raise ConcentrationError(
            f"unknown greek {greek!r}; expected one of {GREEK_AXES}"
        )


@dataclass(frozen=True, slots=True)
class ConcentrationShare:
    """One bucket's share of the total absolute net exposure on a given axis/greek."""

    group_key: str
    net_exposure: float
    abs_share: float


@dataclass(frozen=True, slots=True)
class ConcentrationMetric:
    """Concentration of a book's net Greek exposure over one grouping axis.

    ``herfindahl`` is the Herfindahl-Hirschman index of the absolute-exposure shares:
    ``sum(s_i ** 2)`` where ``s_i`` is bucket ``i``'s share of total absolute net
    exposure. It runs from ``1 / n`` (perfectly even across ``n`` buckets) to ``1.0``
    (everything in one bucket). ``top_share`` is the single largest bucket's share —
    the plain-language "how much sits in one name / one tenor" headline.
    """

    concentration_version: str
    dimension: str
    greek: str
    total_abs_exposure: float
    bucket_count: int
    herfindahl: float
    top_share: float
    top_group_key: str
    shares: tuple[ConcentrationShare, ...]


def _net_exposure(net_delta: float, net_gamma: float, net_vega: float,
                  net_theta: float, greek: str) -> float:
    return {
        "delta": net_delta,
        "gamma": net_gamma,
        "vega": net_vega,
        "theta": net_theta,
    }[greek]


def concentration_metric(
    lines: Iterable[PositionRisk],
    *,
    portfolio_id: str,
    dimension: str,
    greek: str,
) -> ConcentrationMetric:
    """Concentration of one signed Greek across one aggregation axis.

    Buckets the book with the existing ``aggregate_by_key`` (so the axes are exactly
    the canonical underlying / maturity / instrument dimensions), then measures how
    unevenly the *absolute* net Greek is spread across those buckets. Absolute value
    is used so a large long in one bucket and a large short in another both count as
    concentration, not cancellation.
    """
    _validate_greek(greek)
    if dimension not in GROUP_DIMENSIONS:
        raise AggregationError(dimension)

    nets = aggregate_by_key(lines, portfolio_id=portfolio_id, key=dimension)
    exposures = [
        (
            net.group_key,
            _net_exposure(net.net_delta, net.net_gamma, net.net_vega,
                          net.net_theta, greek),
        )
        for net in nets
    ]
    total_abs = math.fsum(abs(value) for _, value in exposures)

    if not exposures:
        raise ConcentrationError(
            f"no positions to measure concentration over for dimension {dimension!r}"
        )

    if total_abs == 0.0:
        # A book with zero net exposure on this greek has no concentration to report;
        # shares are undefined (0/0). Surface that honestly rather than inventing a number.
        shares = tuple(
            ConcentrationShare(group_key=key, net_exposure=value, abs_share=0.0)
            for key, value in exposures
        )
        return ConcentrationMetric(
            concentration_version=CONCENTRATION_VERSION,
            dimension=dimension,
            greek=greek,
            total_abs_exposure=0.0,
            bucket_count=len(exposures),
            herfindahl=0.0,
            top_share=0.0,
            top_group_key="",
            shares=shares,
        )

    shares = tuple(
        ConcentrationShare(
            group_key=key,
            net_exposure=value,
            abs_share=abs(value) / total_abs,
        )
        for key, value in exposures
    )
    herfindahl = math.fsum(share.abs_share * share.abs_share for share in shares)
    top = max(shares, key=lambda share: share.abs_share)
    return ConcentrationMetric(
        concentration_version=CONCENTRATION_VERSION,
        dimension=dimension,
        greek=greek,
        total_abs_exposure=total_abs,
        bucket_count=len(shares),
        herfindahl=herfindahl,
        top_share=top.abs_share,
        top_group_key=top.group_key,
        shares=shares,
    )


@dataclass(frozen=True, slots=True)
class ConcentrationReport:
    """Concentration across every (axis, greek) pair requested for one book."""

    concentration_version: str
    portfolio_id: str
    metrics: tuple[ConcentrationMetric, ...]


def concentration_report(
    lines: Iterable[PositionRisk],
    *,
    portfolio_id: str,
    dimensions: Sequence[str] = GROUP_DIMENSIONS,
    greeks: Sequence[str] = GREEK_AXES,
) -> ConcentrationReport:
    if not dimensions:
        raise ConcentrationError("concentration_report requires at least one dimension")
    if not greeks:
        raise ConcentrationError("concentration_report requires at least one greek")
    line_list = list(lines)
    metrics = tuple(
        concentration_metric(
            line_list, portfolio_id=portfolio_id, dimension=dimension, greek=greek
        )
        for dimension in dimensions
        for greek in greeks
    )
    return ConcentrationReport(
        concentration_version=CONCENTRATION_VERSION,
        portfolio_id=portfolio_id,
        metrics=metrics,
    )
