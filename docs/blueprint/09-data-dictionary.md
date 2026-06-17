> Source: blueprint PDF, pages 35–36. Faithful transcription — see ../blueprint/README.md for governance status.

# Part IX — Data dictionary

| Field | Definition |
|---|---|
| `instrument_key` | Canonical unique key for a tradable contract or underlying. |
| `contract_id_broker` | Broker-specific contract identifier as returned by IBKR. |
| `snapshot_ts` | Timestamp of the normalized market-state snapshot used by analytics. |
| `exchange_ts` | Timestamp associated with the originating market observation when available. |
| `receipt_ts` | Timestamp when the event reached the collector. |
| `reference_spot` | Chosen underlying reference price used for downstream analytics. |
| `reference_type` | Label describing whether `reference_spot` came from mid, last, close, or fallback. |
| `maturity_years` | Year fraction between valuation time and expiry under the chosen day-count convention. |
| `tenor_grid` | The ordered standard maturities analytics project the surface/Greeks onto (P0.1 / OQ-4): `10d`, `1m`, `3m`, `6m`, `12m`, `18m`, `2y`, `3y`. Year fractions under ACT/365: `10/365`, `1/12`, `3/12`, `6/12`, `1`, `1.5`, `2`, `3`. This is the authoritative copy (ADR 0011); `configs/universe.yaml` mirrors it and a test pins their ordered equality. |
| `forward_price` | Chosen forward estimate for a specific maturity. The **primary**, derived term structure (put–call parity, Equations 2–4). |
| `implied_carry` | Carry or dividend-like quantity implied by spot and forward. |
| `futures_price` | Captured listed-futures price mapped onto a pinned tenor; a **secondary**, independently-sourced term-structure estimate (Equation F1). Captured raw — never displaces, smooths, or seeds `forward_price`. |
| `listed_contract_id` | Identifier of the listed futures contract backing a pinned tenor. The listed expiry differs from the pinned tenor and is mapped onto it by a documented roll rule (typed config); recorded per row for audit. |
| `forward_futures_spread` | `futures_price − forward_price` per `(underlying, tenor)`; the forward-vs-futures reconciliation diagnostic. Within the configured per-tenor tolerance $\tau(T)$ it is the expected confirming case; beyond it, a flagged triage record (feeds QC), never an exception or a correction to `forward_price`. |
| `log_moneyness` | $\ln(\text{strike} / \text{forward\_price})$. |
| `mid_option_price` | $(\text{bid} + \text{ask}) / 2$ for the accepted option quote. |
| `implied_vol` | Volatility solved from price and model assumptions. |
| `total_variance` | $\text{implied\_vol}^2$ multiplied by `maturity_years`. |
| `fit_quality_flag` | Indicator describing calibration or QC status. |
| `delta` | First derivative of price with respect to underlying reference spot. |
| `gamma` | Second derivative of price with respect to underlying reference spot. |
| `vega` | Derivative of price with respect to volatility under the chosen unit convention. |
| `theta` | Derivative of price with respect to time under the chosen convention. |
| `dollar_delta` | Delta monetized per \$1 of underlying: Δ·S·mult (×qty per position). Carries an explicit unit string ("$ per $1 of underlying") at the BFF boundary, beside the raw per-unit delta (P0.2 / OQ-1, ADR 0036). |
| `dollar_gamma` | Gamma monetized **per 1% move**: Γ·S²/100 (×mult, ×qty). The 1%-vs-$1 normalisation is the `gamma_normalisation` config flag (default `one_pct`). Carries an explicit unit string ("$ per 1% move") at the BFF boundary, beside the raw per-unit gamma. |
| `dollar_vega` | Vega monetized per **1 vol point** (0.01): vega·0.01·mult (×qty). Carries an explicit unit string ("$ per 1 vol point") at the BFF boundary, beside the raw per-unit vega. |
| `dollar_theta` | Theta monetized per **calendar day**: theta·mult/365 (×qty). The 365-vs-252 day-count is the `theta_day_count` config flag (default 365). Carries an explicit unit string ("$ per calendar day") at the BFF boundary, beside the raw per-unit theta. Additive-nullable on `PricingResult`. |
| `dollar_rho` | Rho monetized per **1% rate**: rho·0.01·mult (×qty). Carries an explicit unit string ("$ per 1% rate") at the BFF boundary, beside the raw per-unit rho. Additive-nullable on `PricingResult`. |
| `risk_free_rate` | Ingested per-currency risk-free rate at a pillar tenor (`rates(currency, pillar_tenor, rate, as_of)` table), as-of dated, continuous/ACT-365. The Eq. 5 `r(T)` **input** and the **risk** rate Rho is bumped against — distinct from the parity-implied pricing-consistency rate. Per-tenor pillar curve; flat-per-currency is the degenerate one-pillar fallback. |
| `implied_riskfree_spread` | `implied_rate − risk_free_rate` per `(currency, tenor)`; a funding/dividend/borrow diagnostic and a QC gate on forward estimation. Beyond a configured bound it is a flagged triage record, never an exception (warn-only until the bound is set from banked history). |
| `scenario_id` | Versioned identifier of a defined stress scenario. |
| `scenario_pnl` | Portfolio or line-level revaluation under `scenario_id`. |
| `var` | **Ratified canon (owner override, 2026-06-17; §02 Eq. 24).** Value-at-risk at a confidence `confidence` ∈ {0.95, 0.99}: the loss at the empirical tail boundary (the `breach_count`-th worst loss) of the per-scenario portfolio-total P&L distribution off the **full-reprice** engine. Stated in loss space (positive = downside); may be negative on an all-profit distribution. |
| `expected_shortfall` | **Ratified canon (owner override, 2026-06-17; §02 Eq. 25).** Expected shortfall / CVaR at `confidence`: the mean of the worst `breach_count` losses — the headline tail metric for this fat-left-tail book. By construction ≥ `var` at the same confidence. |
| `breach_count` | Number of scenarios in the tail at a given `confidence`: `max(ceil((1−confidence)·sample_size), 1)`. The count `var`/`expected_shortfall` are computed over. |
| `tail_risk_version` | Version stamp of the tail-risk engine (`tail-risk-1.0.0`) carried on every `TailRiskReport`. |
| `herfindahl` | **Ratified canon (owner override, 2026-06-17; §02 Eq. 26).** Herfindahl-Hirschman concentration `Σ sᵢ²` of the absolute net-Greek shares over a `dimension` (underlying / maturity / instrument) for one signed greek; ranges `1/n` (even across `n` buckets) to `1` (one bucket holds it all). Absolute shares, so longs and shorts do not cancel. |
| `top_share` | The single largest bucket's share `max sᵢ` of absolute net exposure on a `(dimension, greek)` pair — the "how much sits in one name / one tenor" headline beside `herfindahl`. |
| `exit_sessions` | **Ratified canon (owner override, 2026-06-17; §02 Eq. 27).** Sessions to unwind a position at the allowed participation rate: `|position_size| / (participation_rate · captured_volume)`. `None` when no volume was captured. |
| `liquidity_status` | Exit-difficulty verdict: `ok`, `inexitable` (`exit_sessions` exceeds the configured `max_exit_sessions`, or a real zero-volume session), or `unknown_volume` (no captured `MarketStateSnapshot.volume` to screen against — a surfaced coverage gap, never silently treated as liquid). Screens traded volume only; there is no `open_interest` field. |
| `qc_status` | Pass/warn/fail classification for a named validation check. |
