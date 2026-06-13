> Source: blueprint PDF, pages 29–30. Faithful transcription — see ../blueprint/README.md for governance status.

# Part V — Expanded mathematical notes and engineering guidance

## 1. Forward-engine design details

The forward-engine implementation should be treated as a first-class analytics product. A common mistake is to compute a single parity forward from the apparent ATM strike and move on. That is rarely robust enough for production. Instead, the engine should start by defining an eligibility band around the estimated money region. Within that band, it should build candidate parity forwards per strike, compute liquidity weights, remove outliers, and summarize residuals. The final chosen forward should be accompanied by a confidence score derived from the density and consistency of the candidate set.

The confidence score is useful operationally because not every maturity is equally informative. For example, a maturity with six liquid strikes and tight spreads should produce a high-confidence forward. A maturity with two sparse strikes and wide spreads should produce a low-confidence forward and potentially trigger a fallback policy. The fallback policy should be explicit: either interpolate from neighboring maturities, borrow the previous trusted snapshot, or mark the maturity unusable. The policy chosen should depend on the use case, but the decision must be logged.

$$\text{Mid} = \frac{\text{Bid} + \text{Ask}}{2}, \quad \text{SpreadPct} = \frac{\text{Ask} - \text{Bid}}{\text{Mid}}$$

*Equation 25. Mid-price and spread percentage diagnostics*

### Recommended forward diagnostics

- Candidate count used before and after outlier rejection.
- Median and weighted-mean parity forwards.
- Median absolute deviation of forward candidates.
- Residual of each candidate relative to the chosen forward.
- Forward confidence score and reason code if downgraded.

## 2. Solver engineering details

The inversion engine should separate economics from numerics. Economics determines the admissible price interval and the pricing function. Numerics determine how the root is found. This separation is valuable because it allows the same solver skeleton to be reused with different pricers. For a European option, the root function is the difference between the model price under Black-style dynamics and the market price. For an American option, the same outer skeleton can call a lattice pricer instead.

Use bracketed methods wherever possible for reliability. Newton-type methods can be added as accelerators, but only inside a safe bracket. On illiquid or near-intrinsic quotes, pure Newton iterations can diverge or run into regions where the Vega collapses, making updates unstable. A junior implementer should prioritize a slightly slower but monotone-convergent approach over a faster but brittle solver.

- Lower volatility bound should be near zero but not exactly zero if the pricing function becomes numerically unstable there.
- Upper bound should be high enough to bracket distressed or event-rich markets but not so high that the function loses discrimination.
- Convergence should be measured both in price residual and parameter step size.
- Failed solves should return structured diagnostics, not NaN without context.

## 3. Surface-calibration guidance

A junior developer should think of calibration as a mapping problem: market points in, regularized surface out. The surface is useful only if the mapping is stable. Calibration stability depends at least as much on data hygiene as on the chosen model. Therefore, before tuning a parameterization, inspect the accepted points, quote density by maturity, and residual distribution. In many cases, improvements come from better QC rather than from a more complicated surface model.

When using SVI or any parametric family, store both the calibrated parameters and a reconstructed grid of total variance values. The parameter vector alone is not enough for operations. Operators and downstream services need a directly queryable surface grid, and they need diagnostics that compare the raw accepted points to the reconstructed values. The system should provide both. In sparse maturities, prefer a conservative fallback that is smooth and flagged over an aggressive calibration that produces sharp but unreliable local features.

- Calibrate per maturity slice first; only then handle cross-maturity interpolation.
- Fit in total variance space, not raw volatility, when possible.
- Apply sensible bounds to parameters and log bound hits.
- Keep a fallback nonparametric smoother for sparse slices.
- Always expose fit error metrics and accepted-point counts.

## 4. Greeks methodology

Greeks should be computed using one canonical methodology per product family. If analytic Greeks are available for the chosen pricing model, they should usually serve as the default. If finite-difference Greeks are used, the bump sizes must be versioned and documented. A common source of hidden error is inconsistent bump sizing across modules, which causes the risk engine and scenario engine to disagree for reasons unrelated to economics.

Finite-difference validation should still exist even when analytic Greeks are used. For a regression sample of contracts, compare analytic values with central-difference estimates under fixed bumps. The purpose is not to replace analytic formulas but to catch sign errors, unit mistakes, and accidental inconsistencies in the implementation.

- Define whether vega is per unit volatility or per one volatility point.
- Define whether theta is expressed per calendar day, trading day, or year fraction.
- Ensure monetized Greeks use the correct contract multiplier and currency.
- Store both raw and monetized sensitivities when useful.

## 5. Scenario methodology

Scenarios should be treated as explicit market states, not just as Greek multipliers. The best practice is to support both full repricing and local approximations. Full repricing is slower but more trustworthy and should be the source of truth for the daily scenario report. Local approximations are useful for exploratory intraday monitoring and sanity checks. The exact scenario grid should be version-controlled and retrievable alongside every scenario result.

A good default scenario family includes: parallel spot moves, parallel implied-volatility shifts, a combined spot-and-vol stress, and a small time roll-down for theta-style decay effects. Depending on the products covered, the grid may later add skew twists, forward shifts, or rate shocks. Those can be layered in without changing the architecture as long as the scenario object model is clean from the start.
