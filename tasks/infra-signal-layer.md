# T-signal-layer — persist the strategy-entry signals daily (implied correlation R3, IV rank, RV−IV, term slope)

> **Source:** TARGET §4 ruling **R3** + §7.7 + §1 (the edge chain). The signals are the strategy
> entry inputs; without them there is no rules-based entry, only discretion.

## The gap
No `implied_correlation`/`rho_bar` anywhere in `packages`/`apps`. The Eq-23 basket-variance
primitive lives in `risk/basket.py` but the daily-persisted signals do not exist.

## Scope — persist daily, as-of, per the standard contract discipline:
- **Implied correlation ρ̄ per tenor (R3):** from R2-grade per-name surfaces + the index surface,
  solve Eq 23 (`σ²_index ≈ Σ wᵢ²σᵢ² + Σᵢ≠ⱼ wᵢwⱼσᵢσⱼρ̄`) for ρ̄. The S1 dispersion entry signal +
  a correlation-regime market-state diagnostic.
- **IV rank / percentile per name** (course p.36) — needs banked IV history (the harvested days
  are the raw material). S3 entry input.
- **realized-vs-implied vol spread** per name/tenor.
- **term-structure slope** (front/back, contango) — S5 entry input.

## Depends on
[[ibkr-constituent-option-capture]] (per-name surfaces) + [[infra-per-side-surfaces]] (R2-grade IV).
Banked history depth gates IV rank.

## Done criteria
ρ̄/IV-rank/RV−IV/term-slope persisted daily as-of, contract-typed, look-ahead clean; surfaced as
the strategy-entry inputs; gate green.
