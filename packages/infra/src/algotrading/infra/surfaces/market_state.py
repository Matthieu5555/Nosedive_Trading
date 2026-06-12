"""The per-underlying snapshot market state the projection prices against (M30).

One concern, one module: the reference spot plus the discount-curve resolver — the
label-join precedence, the flat-forward interpolation in ``-ln DF``, and the
nearest-knot extrapolation (F-SURF-01). This is curve logic, not regrid logic, so it
lives apart from :mod:`.projection` (which consumes it cell by cell) and is importable
on its own by any consumer that needs a discount factor at a pinned tenor —
``risk/valuation.py`` derives rates from the same DFs independently.

Extracted verbatim from ``projection.py`` (a pure code move): no arithmetic changed,
no hash exposure — the regen-gated projection golden test pins the bytes.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class SnapshotMarketState:
    """The per-underlying market state a projection prices against, at one snapshot.

    ``spot`` is the underlying reference spot. The discount curve comes in two shapes:
    ``discount_factors_by_tenor`` keyed by **pinned tenor label** (the join that matches by
    construction — preferred when the capture lane provides it), and ``discount_factors``
    keyed by **maturity in years** (the listed-expiry knots the forward estimates priced).
    Carry is taken as zero (the spot==forward, Black-76/futures view) so spot and forward
    delta coincide, matching the delta-band inversion convention; the forward at a tenor is
    then ``spot * discount-free`` — i.e. the forward equals spot here, and the discount
    factor only scales the option price. This is the same ``carry == 0`` pin the 1B
    delta-band selection uses.
    """

    underlying: str
    provider: str
    spot: float
    discount_factors: Mapping[float, float] = field(default_factory=dict)
    default_discount_factor: float = 1.0
    discount_factors_by_tenor: Mapping[str, float] = field(default_factory=dict)

    def discount_factor_for(self, tenor_label: str, maturity_years: float) -> float:
        """The discount factor for a pinned-tenor cell: label binding first, then the curve.

        A ``discount_factors_by_tenor`` entry wins outright — the tenor label is the one
        join key that cannot drift through float re-derivation (F-SURF-01). Without one,
        the factor is read off the maturity-keyed curve via :meth:`discount_factor_at`.
        """
        by_tenor = self.discount_factors_by_tenor.get(tenor_label)
        if by_tenor is not None:
            return by_tenor
        return self.discount_factor_at(maturity_years)

    def discount_factor_at(self, maturity_years: float) -> float:
        """The discount factor at ``maturity_years``, read off the snapshot's DF curve.

        The curve knots are the *listed-expiry* maturities the forward estimates priced,
        while the projection queries the *pinned-tenor* years — the two grids rarely
        coincide, so an exact dict hit cannot be relied on (F-SURF-01: the old exact
        ``get`` silently priced every cell rate-free). Resolution order:

        * an exact key hit returns the stored factor unchanged (bit-for-bit, no log/exp
          round-trip);
        * between knots, the total log-discount ``-ln DF`` is interpolated linearly in
          maturity (flat-forward, the standard curve rule; exact for a flat zero rate);
        * beyond the knot span — and for a single-knot curve — the nearest knot's zero
          rate is held flat, ``DF(T) = exp(-r_nearest · T)``, so ``DF(0) → 1`` rather
          than freezing a long-dated factor onto a short tenor;
        * an **empty** curve falls back to ``default_discount_factor`` — the documented,
          explicitly injected no-curve degradation (the replay paths rely on it), not a
          silent key-miss.
        """
        exact = self.discount_factors.get(maturity_years)
        if exact is not None:
            return exact
        knots = sorted(
            (t, df)
            for t, df in self.discount_factors.items()
            if math.isfinite(t) and math.isfinite(df) and t > 0.0 and df > 0.0
        )
        if not knots:
            return self.default_discount_factor
        times = [t for t, _ in knots]
        log_discounts = [-math.log(df) for _, df in knots]
        if maturity_years <= times[0]:
            return math.exp(-(log_discounts[0] / times[0]) * maturity_years)
        if maturity_years >= times[-1]:
            return math.exp(-(log_discounts[-1] / times[-1]) * maturity_years)
        index = bisect.bisect_left(times, maturity_years)
        span = times[index] - times[index - 1]
        weight = (maturity_years - times[index - 1]) / span
        interpolated = log_discounts[index - 1] + weight * (
            log_discounts[index] - log_discounts[index - 1]
        )
        return math.exp(-interpolated)
