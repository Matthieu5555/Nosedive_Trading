# rates — per-currency risk-free curve `r(T)` (ADR 0054 / R1)

Ingests a per-currency risk-free curve `r(T)` as a daily as-of table, evaluates it at an option's
maturity, makes it the basis **Rho** is bumped against, and persists the **implied − risk-free
spread** diagnostic. Pure functions: no I/O, no clock, no randomness — the caller reads/writes the
store, this package converts, evaluates, and diagnoses.

The two rates are kept **separate by design**: the per-expiry **parity-implied** rate `−ln(DF)/T`
(from `infra/forwards`) stays the *pricing-consistency* rate and is never displaced; the **ingested**
`r(T)` is the *risk* rate. A book-level "rates +50bp" bumps the ingested curve, per currency.

## TL;DR

```python
from algotrading.infra.rates import (
    build_rate_points, curve_from_points, external_curve_rho, implied_riskfree_spread,
)

# 1. Ingest: published levels -> canonical continuous/ACT-365 RiskFreeRatePoint rows (stamped).
points = build_rate_points(
    currency_config=platform_config.rates.for_currency("EUR"),
    published_levels={"euribor_3m": 0.03, "euribor_12m": 0.035},  # source convention
    as_of=trade_date, snapshot_ts=ts, source_snapshot_ts=ts, calc_ts=ts,
    config_hashes=cfg_hashes,
)  # store.write("rates", points)

# 2. Evaluate: rebuild the curve from rows published AS-OF the valuation day (no look-ahead).
curve = curve_from_points("EUR", points)
r_t = curve.rate_at(0.25)            # linear-in-zero-rate interpolation; flat extrapolation

# 3. Risk: Rho against the EXTERNAL curve (∂Price/∂r), bumped per currency.
rho = external_curve_rho(pricing_state, curve)

# 4. Diagnostic + warn-only QC: implied − r(T) per (currency, tenor).
diag = implied_riskfree_spread(
    currency="EUR", maturity_years=0.25, implied_rate=fwd.implied_rate,
    risk_free_rate=r_t, abs_bound=0.02, disposition="warn",
)  # diag.qc_status in {"ok","warn","fail"}; warn-only by default
```

## What each piece does

- **`conventions.to_continuous_act365`** — converts a published money-market rate (simple/ACT-360,
  etc.) to the canonical **continuous compounding / ACT-365** zero rate via the no-arbitrage growth
  factor. An already continuous-ACT/365 source is unchanged (identity).
- **`curve.RateCurve`** — immutable zero-rate pillars. `rate_at(T)` interpolates **linearly in the
  zero rate** between pillars and **flat-extrapolates** beyond the ends. `flat()` is the degenerate
  single-pillar curve (the term-structured generalisation of `ForwardConfig.rate`).
- **`ingest.build_rate_points` / `curve_from_points`** — config + levels → stamped
  `RiskFreeRatePoint` rows (unpublished pillars are a coverage gap, not a defect); and rows →
  evaluator. The **as-of filter is the caller's job**: pass only rows published as-of the valuation
  day, so a past-day reconstruction never reads a later curve (no look-ahead).
- **`rho.external_curve_rho`** — Rho as the sensitivity to the external curve, by a symmetric
  finite-difference reprice that holds the forward fixed and moves only the discounting. The
  parity-implied rate is never touched.
- **`spread.implied_riskfree_spread`** — the `implied_rate − r(T)` funding/dividend/borrow diagnostic
  plus a QC verdict. A breach beyond the configured bound is **WARN-only by default** (tune the bound
  from banked history; `disposition="fail"` makes it a hard gate).

## Conventions, pillars, and the bound are typed config

`configs/rates.yaml` (`RatesConfig`, ADR 0028) names the per-currency source, the pillar set + their
`maturity_years`, the source day-count/compounding (converted on ingest), the interpolation
convention, and the warn-only spread-QC bound. Never a `.py` literal. The `rates` config lives in its
own `config_hashes["rates"]` bundle, so adding it leaves the pricing/forward goldens byte-identical;
the canonical `ForwardConfig.rate: null` path stays byte-identical parity-implied.
