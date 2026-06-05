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
| `forward_price` | Chosen forward estimate for a specific maturity. |
| `implied_carry` | Carry or dividend-like quantity implied by spot and forward. |
| `log_moneyness` | $\ln(\text{strike} / \text{forward\_price})$. |
| `mid_option_price` | $(\text{bid} + \text{ask}) / 2$ for the accepted option quote. |
| `implied_vol` | Volatility solved from price and model assumptions. |
| `total_variance` | $\text{implied\_vol}^2$ multiplied by `maturity_years`. |
| `fit_quality_flag` | Indicator describing calibration or QC status. |
| `delta` | First derivative of price with respect to underlying reference spot. |
| `gamma` | Second derivative of price with respect to underlying reference spot. |
| `vega` | Derivative of price with respect to volatility under the chosen unit convention. |
| `theta` | Derivative of price with respect to time under the chosen convention. |
| `dollar_gamma` | Gamma monetized by spot squared and multiplier. |
| `scenario_id` | Versioned identifier of a defined stress scenario. |
| `scenario_pnl` | Portfolio or line-level revaluation under `scenario_id`. |
| `qc_status` | Pass/warn/fail classification for a named validation check. |
