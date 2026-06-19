> Source: blueprint PDF, pages 49–51. Faithful transcription — see ../blueprint/README.md for governance status.

# Part XIX — Final implementation reminders

The purpose of this final section is to prevent a junior engineer from taking common shortcuts that appear harmless but later make the platform unreliable. The project should be treated as a product with users, audits, and operational obligations. Every shortcut that erodes determinism or lineage is therefore costly, even if it appears to save time in the first week of implementation.

- Do not compute a forward, implied volatility, or Greek in an exploratory notebook and then copy the formula into production later. Production logic should be implemented in a tested library first and used by notebooks second.
- Do not overwrite historical analytics partitions without writing a new version identifier. Reproducibility requires visible history.
- Do not hide fallback behavior. If the system used last price instead of mid, or interpolated a missing maturity, that fact must be queryable.
- Do not allow the collector to perform expensive CPU-bound work. Separate ingestion from analytics.
- Do not merge strategy-specific assumptions into generic platform code. Keep the infrastructure reusable and opaque with respect to trading intent.
- Do not sign off on a release without replaying at least one calm day and one stressed day from the regression library.

## Appendix E — Canonical field mapping checklist

The table below is intended as an implementation checklist during schema design and code review. Every row should exist somewhere in the codebase, with a single authoritative definition. This appendix exists because field ambiguity is one of the most persistent causes of downstream confusion in risk systems.

| Field | Layer | Type | Implementation note |
|---|---|---|---|
| event_id | raw | string | Unique within collector session; never recycled |
| collector_session_id | raw | string | Connects raw events to a collector run summary |
| instrument_key | all | string | Canonical key used across all tables |
| contract_id_broker | master/raw | string | Foreign key to broker-specific representation |
| field_name | raw | string | Name of observed field such as bid, ask, last, gamma |
| field_value | raw | numeric/string | Typed at normalization boundary |
| receipt_ts | raw | timestamp | UTC time when collector received the event |
| canonical_ts | snapshot | timestamp | Time used to align a snapshot |
| reference_spot | snapshot | numeric | Chosen spot used by downstream analytics |
| reference_type | snapshot | string | mid / last / close / fallback |
| quote_age_seconds | snapshot | numeric | Age of quote at snapshot time |
| mid_option_price | snapshot | numeric | Computed from bid and ask when eligible |
| forward_price | forward | numeric | Chosen forward per maturity |
| forward_confidence | forward | numeric | Quality score from candidate diagnostics |
| implied_vol | iv_points | numeric | Solved volatility |
| solver_converged | iv_points | bool | True only if convergence criteria met |
| surface_model | surface | string | SVI, spline, or other configured method |
| fit_rmse | surface | numeric | Fit error for slice or surface |
| greek_delta | risk | numeric | Canonical delta |
| greek_gamma | risk | numeric | Canonical gamma |
| greek_vega | risk | numeric | Canonical vega |
| dollar_gamma | risk | numeric | Monetized gamma using multiplier |
| scenario_id | scenario | string | Versioned scenario key |
| scenario_pnl | scenario | numeric | Repriced PnL under scenario |
| qc_status | qc | string | pass / warn / fail |
| reason_code | qc | string | Stable diagnostic reason code |
