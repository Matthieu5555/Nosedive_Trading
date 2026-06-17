"""Day-count / compounding conversion to the canonical continuous-ACT/365 rate (ADR 0054, RULED 4).

The platform's internal canonical rate convention is **continuous compounding under ACT/365**,
consistent with `maturity_years` and Black-76. Published money-market rates are usually *simple*
(linear) under ACT/360 (€STR / SOFR / short Euribor) or ACT/365. This module converts a source rate
to the canonical continuous-ACT/365 zero rate on ingest, so the curve and every downstream consumer
speak one convention.

The conversion is anchored on the **no-arbitrage growth factor** over the tenor: a simple rate
`r_src` for an accrual fraction `tau_src = days / src_basis` grows money by `(1 + r_src * tau_src)`;
the equivalent continuous ACT/365 rate `r_c` over the canonical fraction `tau_365 = days / 365`
satisfies `exp(r_c * tau_365) = 1 + r_src * tau_src`. A source already quoted continuously only
needs its day-count rebased.
"""

from __future__ import annotations

import math

DAY_COUNTS = ("ACT/365", "ACT/360")
COMPOUNDINGS = ("continuous", "simple")

_BASIS_DAYS = {"ACT/365": 365.0, "ACT/360": 360.0}
_CANONICAL_BASIS_DAYS = 365.0


class RateConventionError(ValueError):
    """A source rate cannot be converted to the canonical continuous-ACT/365 convention."""


def _basis_days(day_count: str) -> float:
    try:
        return _BASIS_DAYS[day_count]
    except KeyError:
        raise RateConventionError(
            f"unknown day_count {day_count!r}; expected one of {DAY_COUNTS}"
        ) from None


def to_continuous_act365(
    source_rate: float,
    maturity_years: float,
    *,
    source_day_count: str,
    source_compounding: str,
) -> float:
    """Convert a published `source_rate` to the canonical continuous-ACT/365 zero rate.

    `maturity_years` is the pillar's tenor expressed canonically (ACT/365 year fraction); it both
    selects the tenor and carries the day-count rebase. A `simple` source converts through the
    growth factor; a `continuous` source is rebased by the day-count ratio only. The conversion is
    exact and leaves an already-continuous-ACT/365 rate **unchanged** (the identity case).
    """
    if not math.isfinite(source_rate):
        raise RateConventionError(f"source_rate must be finite, got {source_rate!r}")
    if not (math.isfinite(maturity_years) and maturity_years > 0.0):
        raise RateConventionError(
            f"maturity_years must be a finite positive year fraction, got {maturity_years!r}"
        )
    if source_compounding not in COMPOUNDINGS:
        raise RateConventionError(
            f"unknown source_compounding {source_compounding!r}; expected one of {COMPOUNDINGS}"
        )

    src_basis = _basis_days(source_day_count)
    # Accrual fraction under the SOURCE day-count: the canonical ACT/365 fraction implies a day
    # count `days = maturity_years * 365`, which the source measures over its own basis.
    day_count_ratio = _CANONICAL_BASIS_DAYS / src_basis  # tau_src / tau_365
    tau_src = maturity_years * day_count_ratio

    if source_compounding == "simple":
        growth = 1.0 + source_rate * tau_src
        if growth <= 0.0:
            raise RateConventionError(
                f"simple source_rate {source_rate!r} over tau={tau_src!r} implies a non-positive "
                "growth factor; cannot take a continuous log"
            )
        return math.log(growth) / maturity_years

    # Continuous source: r_c * tau_365 = source_rate * tau_src, hence the day-count rebase only.
    return source_rate * day_count_ratio
