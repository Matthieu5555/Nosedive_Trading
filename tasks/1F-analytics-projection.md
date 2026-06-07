# 1F — Analytics projection: (tenor × delta-band) grid, Greeks decimal + dollar

> **Phase 1, after 1C.** The pure engines exist and are built — `infra/{forwards,iv,surfaces,
> pricing,risk}`. What is missing is the *projection*: cross-maturity regrid onto the **pinned
> tenor set** crossed with a **delta-band axis**, with every option carrying **both** Greek
> representations (decimal per-unit + dollar, unit-tagged). The blueprint (ADR 0011) overrides this
> spec on every formula, tenor, grid axis, and $-convention; where this file and the blueprint
> disagree, the blueprint wins.

- **Owns:** a new projection module under `packages/infra/src/algotrading/infra/surfaces/` (the
  tenor×delta-band regrid; `fit.py` today only projects one slice onto a **log-moneyness** bucket
  grid via `project_surface_fit` / `surface_grid_cells` — there is no cross-maturity tenor regrid
  and no delta axis), the `ProjectedOptionAnalytics` contract added to
  `packages/infra/src/algotrading/infra/contracts/tables.py` (+ registry/serialization in
  `contracts/registry.py`), the storage wiring for that table in `infra/storage/`, the dollar-Greek
  layer + unit strings, the projection config (tenor grid, delta bands, gamma/theta flags), and the
  tests + golden fixtures. Conforms to **[ADR 0019](../.agent/decisions/0019-one-immutable-raw-model.md)**,
  **[ADR 0029](../.agent/decisions/0029-contract-field-names-conform-to-blueprint.md)** (field names),
  **[ADR 0033](../.agent/decisions/0033-analytical-storage-duckdb-polars-over-parquet.md)** (storage), and
  **[ADR 0034](../.agent/decisions/0034-data-retention-compaction-and-backend-disposition.md) §4** (partition layout).
- **Depends on:** **1C** (captured daily close snapshots to project) and **P0** (P0.1 the pinned
  tenor grid + P0.2 the $-unit ruling/flags). Reuses the built per-maturity SVI fits (`fit_slice`),
  the surface evaluator (`svi.py` `total_variance`), the pricing/Greeks engine (`pricing/black76.py`),
  and the dollar-Greek formulas already in `risk/greeks.py`. **D1** must have landed the `provider`
  partition segment before this table is written at scale (D1 lists `ProjectedOptionAnalytics` as a
  1F-gated new table).
- **Blocks:** **1H** (QC of the grid — coverage floor per tenor, Δ-band completeness), **1I** (front
  page 1 serves this grid: 3D surface, dollar Greeks, smile per maturity).
- **State going in (audited 2026-06-07):** `surfaces/fit.py` has `project_surface_fit()` projecting
  one SVI/nonparametric slice onto a fixed **log-moneyness** bucket grid (default
  `(-0.2,-0.1,0,0.1,0.2)`) and `fit_slice()` fitting one SVI smile per maturity. There is **no**
  tenor×delta-band grid and **no** cross-maturity regrid onto a fixed tenor set. The contracts carry
  the ADR-0029 names (`forward_price`, `implied_vol`, `log_moneyness`, `scenario_pnl`, `qc_status`,
  `dollar_delta/gamma/vega`); `PricingResult` already carries `dollar_delta/dollar_gamma/dollar_vega`
  but no `dollar_theta`/`dollar_rho` and no unit strings. `risk/greeks.py` documents the dollar
  formulas (Eq 17/18) at the position level. No `ProjectedOptionAnalytics` contract exists yet.

## Objective

Produce, for one underlying at one daily snapshot, a deterministic grid over the **pinned tenor set
× delta band**, each cell carrying the fitted IV, the model price, the full Greeks in **both
representations side by side — decimal (raw per-unit) and dollar — each dollar number tagged with an
explicit unit string** (OQ-1 / P0.2). The decimal per-unit Greeks are the source of truth; the
dollar layer is derived. Output is a typed `ProjectedOptionAnalytics` contract, stamped and stored
per ADR 0019/0033/0034. The grid output must match committed golden fixtures byte-for-byte. No
look-ahead: every cell uses only the surface fit and market state from that snapshot — never a future
snapshot, never an interpolation across the snapshot boundary in time.

The pinned axes (blueprint OQ-4 / P0.1, blueprint §2 — the blueprint overrides if it differs):
- **Tenor grid:** `10d, 1m, 3m, 6m, 12m, 18m, 2y, 3y`.
- **Delta band:** every listed strike from the **30Δ put, through ATM, to the 30Δ call** (the whole
  central smile, not three pillars). Bucketed/labeled by delta; band edges and any bucket centers are
  config, defaulting to the blueprint's 30Δ–ATM–30Δ window.

The $-Greek layer (OQ-1 / P0.2 — blueprint overrides; raw per-unit is source of truth):
- `dollar_delta  = Δ · S · mult`        (per $1 move)
- `dollar_gamma  = Γ · S² / 100`        (per 1% move) — **config flag: 1%-move vs $1-move**
- `dollar_vega   = Vega · 0.01 · mult`  (per 1 vol point)
- `dollar_theta  = Θ · mult / 365`      (per calendar day) — **config flag: 365 vs 252 day-count**
- `dollar_rho    = Rho · 0.01 · mult`   (per 1% rate)

Use the **ADR-0029 `dollar_*` field names** (never `cash_*`). Each dollar field is paired with a unit
string (e.g. `"USD per $1 spot move"`, `"USD per 1% spot move"`, `"USD per 1 vol pt"`, `"USD per
calendar day"`, `"USD per 1% rate"`) so the number is self-describing on the row and the BFF/front
(1I) renders the unit without re-deriving it. The gamma-normalisation and theta-day-count flags must
flow from validated config (C7 pattern), not `.py` literals, and must enter the provenance
`config_hashes`.

## What to do (ordered)

1. **Define the `ProjectedOptionAnalytics` contract** in `contracts/tables.py` (frozen dataclass,
   slots, same shape as the other derived contracts). Carry at minimum: `snapshot_ts`, `underlying`,
   `tenor_label` (one of the pinned eight), `maturity_years`, `delta_band` (label) + signed target
   `delta`, `log_moneyness`, `strike`, `forward_price`, `implied_vol`, `total_variance`, `price`, the
   decimal Greeks (`delta, gamma, vega, theta, rho`), the dollar Greeks
   (`dollar_delta, dollar_gamma, dollar_vega, dollar_theta, dollar_rho`) **each paired with a `*_unit`
   string label** (`dollar_delta_unit`, …), sourced as a fixed **lookup from the P0.2 metric-contract
   config** (not a free-form per-row value),
   `model_version`/`pricer_version`, `source_snapshot_ts`, and a `provenance` stamp. Register it
   (registry + serialization round-trip) using the **additive** schema-evolution path so the table
   joins the rest cleanly. Mark it provider-partitioned in the D1 registry.

2. **Build the tenor regrid.** Reuse the per-maturity SVI fits from `fit_slice`. The pinned tenors
   rarely coincide with listed expiries, so regrid in **total-variance** space along maturity
   (calendar-no-arb-respecting interpolation — total variance non-decreasing in maturity, blueprint
   Eq 21), not in raw vol. For a target tenor outside the fitted maturity span, **do not extrapolate
   silently** — emit a labeled gap (a structured diagnostic / sentinel `qc_status`-style flag),
   never a bare NaN (1H consumes these gaps). The interpolation rule and any clamp are config + go in
   the blueprint-conformant docs.

3. **Build the delta axis.** For each (underlying, tenor) evaluate the regridded smile to recover the
   strike/log-moneyness for each delta-band point: invert the option delta (from `pricing/black76.py`
   — spot-delta convention as documented there) against the fitted IV to land the **30Δ put → ATM →
   30Δ call** window. Solve delta→strike consistently with the surface (the IV at the solved strike
   is the IV used to price it — no mismatch). Out-of-band or non-listed targets are labeled gaps, not
   guesses.

4. **Price + Greeks per cell, both representations.** For each grid cell, price with the Black-76
   engine at the cell's `(forward_price, strike, maturity_years, implied_vol)`; take the decimal
   per-unit Greeks as source of truth; derive the dollar layer with the five formulas above, reading
   the gamma/theta flags from config, and attach the unit strings. Reuse `risk/greeks.py` formulas
   rather than re-deriving (keep one home for the dollar math); if 1F needs `dollar_theta`/`dollar_rho`
   not yet on `PricingResult`, add them there too (additive) so there is no second dollar-Greek code
   path.

5. **Stamp + store.** Every cell shares the snapshot's provenance stamp with complete `config_hashes`
   (tenor grid, delta bands, interpolation rule, gamma/theta flags). Write via the storage adapter to
   the ADR-0034 §4 layout
   `<root>/analytics/projected_option_analytics/provider=<P>/trade_date=<D>/underlying=<SYM>[/version=<V>]/data.parquet`;
   `code_version`/`config_hash` stay in the stamp/manifest, never in partition dirs; restatement is the
   `version=<V>` segment.

6. **Wire the projection entrypoint** as a pure function `(snapshot market state + per-maturity fits +
   projection config) → tuple[ProjectedOptionAnalytics, ...]`, injected config (C7 DI pattern), no
   YAML read deep in compute. The actor/orchestration (1G cron) calls it and persists what comes back.

7. **Golden fixtures + regeneration command.** Commit golden output for a small hand-checked
   underlying/snapshot and a single documented regeneration command (deliberate, reviewable — never
   auto-regenerated). uv only for every command (`uv run …`).

## Test surface

Read [TESTING.md](TESTING.md). The independent-oracle, golden-file, determinism, edge-case, and
seam rules there are mandatory; the cases specific to 1F:

- `test_tenor_grid_is_the_pinned_eight` — the tenor axis is exactly `10d,1m,3m,6m,12m,18m,2y,3y`, in
  that order; a config drift to a different set fails loudly.
- `test_delta_band_spans_30d_put_to_30d_call` — the delta axis covers the 30Δ-put→ATM→30Δ-call
  window; ATM and both 30Δ edges are present; an out-of-band target is a **labeled gap, not a NaN**.
- `test_dollar_greeks_match_hand_values` — independent oracle: for a fixture with known
  `(Δ,Γ,Vega,Θ,Rho,S,mult)`, the five dollar Greeks equal the hand-computed `Δ·S·mult`, `Γ·S²/100`,
  `Vega·0.01·mult`, `Θ·mult/365`, `Rho·0.01·mult` (values derived in the test comment, not from the
  code under test), within float tolerance.
- `test_gamma_flag_1pct_vs_dollar` and `test_theta_flag_365_vs_252` — flipping each config flag
  changes exactly that dollar number by the expected factor and nothing else.
- `test_dollar_greeks_carry_unit_strings` — every dollar field has its expected unit string; decimal
  per-unit Greeks are present beside the dollar ones (both representations side by side).
- `test_tenor_interpolation_is_calendar_no_arb` — property test (Hypothesis): total variance
  non-decreasing in maturity across the regrid (blueprint Eq 21) over random fitted slices.
- `test_no_lookahead_in_projection` — the cell at snapshot D uses only D's state/fits; injecting a
  later snapshot does not change any cell (run `check-lookahead-bias` on the module).
- `test_projection_golden_byte_identical` — recompute the committed golden grid and compare
  byte-for-byte; a separate-process hash check on the stamp `config_hashes` (no `PYTHONHASHSEED`
  reliance).
- `test_reordering_invariance` — shuffling the input fits/strikes leaves the grid identical.
- Edge cases (the floor): empty chain, single listed expiry (cannot span the tenor grid → labeled
  gaps), one-point slice, a tenor beyond the fitted span (labeled gap, no extrapolation), a strike
  exactly at a band edge, NaN/inf inputs rejected with a structured diagnostic.
- Contract/seam: `ProjectedOptionAnalytics` round-trips through the storage adapter and validates
  against the registry schema (C→A seam); at least one malformed instance is rejected by write-ahead
  validation with an explicit error, not a silent coercion. Two providers writing the same
  `(underlying, trade_date)` land in disjoint partitions (D1 invariant).
- Branch coverage on the new pure projection module at or above the committed floor (≥90%).
- Gate green: `uv run ruff … && uv run mypy … && uv run lint-imports && uv run pytest`.

## Done criteria

For one underlying at one snapshot, the projection emits a deterministic grid over the pinned
**tenor × delta band**; every cell carries decimal **and** dollar Greeks side by side, each dollar
number unit-tagged with the ADR-0029 `dollar_*` names; the gamma-1%/theta-365 flags are config-driven
and enter `config_hashes`; tenor regrid is calendar-no-arb and never silently extrapolates; gaps are
labeled, never bare NaN; output matches the committed golden fixtures byte-for-byte and is stable
across processes and input reordering; the `ProjectedOptionAnalytics` contract round-trips and stores
under the ADR-0034 §4 provider-partitioned layout; no look-ahead; root gate green (uv only).

## Gotchas

- **Blueprint (ADR 0011) overrides** every tenor, delta-band edge, formula, and $-convention in this
  file. If the blueprint data dictionary differs (e.g. ATM definition, delta convention), follow it
  and note the divergence — do not encode this file's defaults over it.
- **Delta convention must match the pricer.** `pricing/black76.py` is **spot delta** — invert delta→
  strike with the same convention or the band lands on the wrong strikes. State the convention in the
  contract/docs.
- **One dollar-Greek home.** The formulas live in `risk/greeks.py` (position level) — reuse them;
  don't fork a second copy for the projection. Add `dollar_theta`/`dollar_rho` there (additive) if
  missing rather than computing them inline here.
- **No silent extrapolation past the fitted maturity/strike span** — that is a labeled gap 1H acts
  on, not a guessed number. Same for a tenor with no nearby listed expiry.
- **Regrid in total-variance space, not raw vol** — interpolating vol directly can violate calendar
  no-arb (Eq 21); the property test will catch it.
- **`-0.0`/`10` vs `10.0`/`NaN` discipline** in the stamp hash (C7 hardening) — the golden grid must
  be byte-identical across two processes without `PYTHONHASHSEED`.
- **D1 first at scale:** do not write this table at equity scale before D1's `provider` segment lands;
  crypto-only (DERIBIT) works meanwhile.
- **uv only** for every command in tests, fixtures regeneration, and the gate. No bare `python`/`pip`.
