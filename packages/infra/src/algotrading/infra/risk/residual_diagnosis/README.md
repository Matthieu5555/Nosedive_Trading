# residual_diagnosis

Names the unmodeled exposure behind the attribution residual (TARGET §5.2, §7 #10).

Realized day-over-day P&L is decomposed by `risk/attribution.py` into the named
Taylor terms (delta..volga). The leftover — the **residual** — is the part the Greek
model cannot name. §5.2 says that leftover is itself data: regress it against candidate
unmodeled exposures and report which one the book silently carries. This package is the
home for that diagnosis. It crosses from deterministic decomposition into **statistical
inference**, so it carries the full §6 quant-guard bar: out-of-sample / walk-forward,
no data-snooping, as-of everywhere.

## What is built

Two parts — one fully live today, one deliberately gated.

### 1. Residual time-series persistence (`persistence.py`, `covariates.py`)

`ResidualObservation` (contract `residual_observations`, layer `derived`,
**append-only**) banks, per `(as_of_date, portfolio_id, level)`:

- the realized residual and the named Taylor terms it is the remainder of, and
- the candidate unmodeled-exposure covariates observable **as-of** that day:
  skew (`svi_rho` from the per-side surfaces), regime + vol-of-vol (from the signal
  layer's `iv_rank`), and liquidity/slippage proxies (reserved for when a fills-based
  position store lands).

Every covariate is `float | None`. An exposure that could not be observed as-of is
recorded as `None` — **honest absence, never a fabricated zero**. `read_residual_series`
is point-in-time: it returns only rows dated on or before `as_of`, so a past day's
diagnosis can never see a future residual. This is buildable and tested today; it
accumulates banked depth one trading day at a time.

### 2. Gated walk-forward regression (`regression.py`, `diagnose.py`)

`diagnose_residual` regresses the banked residual on the candidate factors with a single
expanding-origin walk-forward split (train on the leading/past window via
`scipy.linalg.lstsq`, score on the held-out trailing/future window). It **refuses** to
name a dominant exposure until a configured minimum banked depth is met:

- `RegressionConfig.min_oos_days` (default **10**) is the out-of-sample floor.
- the training floor is derived from the factor count
  (`(n_factors + 1) × min_train_rows_per_factor`, default 5 per coefficient).
- for the 6 candidate factors the total floor is **45 banked days** (35 train + 10 OOS).

Below the floor it returns `DiagnosisStatus.GATED` with a precise reason and **no
coefficients** — the honest refusal §6 mandates, not a fabricated finding. Above it,
it names the `dominant_factor` and reports out-of-sample R².

## Why the live path is gated today

The verdict over canonical data is currently **gated** because the inputs are shallow:
3 analytics trade-dates on disk, **no banked residual series**, and **no fills/book
partitions** (so no week-plus of realized P&L). Regressing that thin, friendly sample is
exactly the data-snooping §6 forbids. The estimator math is nonetheless proven correct:
`test_residual_diagnosis.py` plants a known linear relationship on synthetic data and
shows the walk-forward fit recovers the planted coefficients within tolerance, while the
live path against a shallow store returns `GATED`.

The gate is honored by **refusing the live verdict**, not by refusing to build — the
persistence and the proven-on-synthetic estimator are real, landable infrastructure that
the diagnosis will run on once enough days are banked.

## Entry points

- `persist_residual_observations` / `read_residual_series` — bank and as-of read.
- `read_covariates_as_of` — assemble the as-of candidate covariates.
- `observation_from_realized` — turn a `RealizedBookAttribution` + covariate reading
  into a banked `ResidualObservation`.
- `diagnose_residual_as_of` — the live, gated path over the store.
- `diagnose_residual` — the gated estimator over an in-memory design.
