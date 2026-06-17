# 0057 — residual_observations contract + gated residual-diagnosis estimator

- **Status:** accepted
- **Date:** 2026-06-17
- **Source:** TARGET §5.2 (the residual "is not only a gate — it is the next signal") +
  §7 #10; `tasks/infra-residual-diagnosis.md`; tech-lead ruling (owner override of the
  spec's "do not start" deferral).

## Context

Realized day-over-day attribution (`risk/attribution.py`) decomposes realized P&L into
the named Taylor terms and leaves a **residual** the Greek model cannot name. §5.2 wants
that residual *diagnosed*: regress it against candidate unmodeled exposures and report
which one the book silently carries. This is the one attribution step that crosses from
deterministic decomposition into **statistical inference**, so §6 raises the bar —
out-of-sample / walk-forward, no data-snooping, as-of everywhere.

The spec defers the step behind two hard prerequisites: (1) the §7 #1 booking chain →
fills-based position store → real realized P&L, and (2) a week-plus of banked realized
attribution. Prerequisite (1)'s **code** has landed. Prerequisite (2) has **not**:
measured on disk there are 3 analytics trade-dates, no fills/book partitions, and no
persisted residual time series. Regressing that thin, friendly sample is exactly the
data-snooping §6 forbids.

The owner overrode the deferral and authorized the honest middle path: build what
genuinely exists and is correct without banked depth, and **gate** the part that needs
depth so it refuses rather than fabricates.

## Decision

1. **New contract `residual_observations`** (layer `derived`, append-only; primary key
   `(as_of_date, portfolio_id, level)`). One as-of row banks the realized residual, the
   named Taylor terms it is the remainder of, and the candidate unmodeled-exposure
   covariates observable as-of that day (skew from the per-side surfaces; regime and
   vol-of-vol from the signal layer; liquidity/slippage reserved for the fills store).
   Every covariate is `float | None` — an exposure unobservable as-of is `None`, **never
   a fabricated zero**. This is the "raw material" persistence §5.2 asks for; it
   accumulates banked depth one trading day at a time. The as-of read returns only rows
   dated on or before the as-of date (no look-ahead), mirroring the signal layer.

2. **The regression is gated on a configured minimum out-of-sample depth.** A documented
   walk-forward residual regression (`scipy.linalg.lstsq`, not a hand-rolled OLS) refuses
   to name a dominant exposure until `RegressionConfig.min_oos_days` out-of-sample rows
   plus a factor-count-derived training floor are banked. The default gate is 10
   out-of-sample days; with the 6 candidate factors the total floor is 45 banked days
   (35 train + 10 OOS), comfortably beyond a "week-plus" and far beyond the 3 trade-dates
   on disk. Below threshold the diagnosis returns `GATED` with a precise reason and no
   coefficients. The estimator math is proven correct against synthetic data with a known
   planted coefficient; the **live** path over canonical data stays gated because
   canonical depth is insufficient.

3. **The spec's "do not start before inputs exist" warning is honored by gating the live
   verdict, not by refusing to build.** The persistence and the proven-on-synthetic
   estimator are real, landable infrastructure that the diagnosis runs on once enough
   days are banked.

## Consequences

- The residual stops being an invisible on-demand scalar: it has a contract-typed,
  as-of banked home that future days fill.
- No fabricated finding can leak out: the gate is the only path to a verdict, and it
  refuses below depth. `check-lookahead-bias` is clean on the historical path.
- When the fills-based position store banks a week-plus of realized attribution, the
  diagnosis becomes live by passing the depth gate — no further infrastructure change,
  only banked data and (if desired) liquidity/slippage covariate wiring.
- The gate threshold `N` is a typed config knob with a defensible default that scales
  with the candidate-factor count; it can be tuned without code changes.
