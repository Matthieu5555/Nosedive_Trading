# T-capture-coverage-panel — show captured-data quality at a glance (per-expiry + per-tenor)

> **Owner ask (2026-06-12).** The front never shows the captured option chain as a plain table,
> so data-quality problems are invisible behind the smoothed surface — the tenor-selection bug
> (only front-month expiries, 1m…3y empty) sat unseen for a day. A coverage table makes it
> obvious in one glance. Directly serves the TASKBOARD review-priority #1 ("verify tonight's EOD
> captures are 100% clean and fully populated"): this is the operator's tool to *see* it.
>
> **3-onglets home (2026-06-17):** data-quality belongs on **Onglet 1 (Données)**. The old
> `Market.tsx` mount moved when `c4ce734` rebuilt page 1 — re-confirm placement against the locked
> [frontend-3onglets-target-ux](frontend-3onglets-target-ux.md) before the phase-2 quote-completeness
> add (the consolidation may host it as a secondary utility, not a primary block).

## Why this is cheap

The data-quality signal is **already on disk** — nothing new to compute, only to surface:

- `instrument_master` (`data/raw/instrument_master/trade_date=<D>/underlying=<U>/`) — the captured
  chain: every contract's expiry / strike / right. Per-expiry counts and strike range derive from it.
- `qc_results` (`data/qc/qc_results/trade_date=<D>/`) — the verdicts WS 1H **already computes**:
  `tenor_coverage_floor` (with `breaching_tenors` = per-tenor measured-vs-floor),
  `option_chain_coverage`, `delta_band_completeness`. Today `routers/health.py:_qc_status_for`
  (line 35-42) **reduces all of this to a single pass/fail** — the per-tenor detail is thrown away
  before it reaches the front.
- (optional, phase 2) `raw_market_events` — quote completeness (% of contracts with a non-null
  bid *and* ask), the cheapest liquidity proxy.

## Objective

A read-only **Capture Coverage** view: for a `(underlying, trade_date)`, a table of **what was
captured per expiry** alongside **the per-tenor QC coverage** (including the empty tenors), so an
operator sees term-structure gaps, thin strikes, and QC breaches without opening the surface.

## Owns

- **BFF**: a new `apps/frontend/src/algotrading/frontend/routers/coverage.py` →
  `GET /api/coverage?underlying=<U>&trade_date=<D>` (trade_date optional, defaults to the latest
  with `instrument_master` data, mirroring `health.py`). Plus a serializer in `serializers.py`.
- **Front**: a `apps/frontend/web/src/components/CoverageTable.tsx` (reuse the existing table idiom —
  `MaturityAccordion.tsx` / `BasketLegGrid.tsx` / `ConstituentTable.tsx`) and its TS types in
  `api.ts`, rendered as a panel on the Market (Tab 1) page.
- Tests on both sides.

## Depends on / coordinates with

- Nothing blocking. **File coordination (shared tree):** the new router + component + serializer are
  disjoint, but wiring the panel touches **`App.tsx` / `Market.tsx`**, which the front agent
  (codex) is editing — claim on the TASKBOARD and coordinate that one routing/placement edit.
- Reads only already-persisted tables; **no new capture/compute**.

## What to do (ordered)

1. **BFF endpoint.** Read `instrument_master` for `(trade_date, underlying)` and group by expiry →
   `{expiry, n_strikes, n_calls, n_puts, strike_min, strike_max}`. Read `qc_results` for the date
   and extract the coverage checks (`tenor_coverage_floor.breaching_tenors`,
   `option_chain_coverage`, `delta_band_completeness`) into `{tenor, measured, floor, status}` rows
   covering **the whole pinned grid** (so a tenor with zero captured expiries shows as a row with
   `measured=0`, not an omission). Return both blocks plus the overall `qc_status`. An absent
   partition → a labeled empty payload (200 with `n_expiries=0`), never a 500.
2. **Map captured expiry → pinned tenor (display aid).** Tag each captured expiry with the nearest
   pinned tenor (reuse `tenor_target_dates` / `surfaces.tenor_years` — the single home of the
   label→year map) so the table reads "this expiry serves ~10d", and the empty tenors are visible as
   their own zero-rows. Do **not** re-implement the tenor map.
3. **Front table.** Two sections in one panel: (a) captured expiries (sortable by date), (b)
   per-tenor coverage with the QC status badge (✓ / ⚠ / ✗). Empty state: "No capture for this date".
4. **Place it on Tab 1.** A collapsible "Capture coverage" panel under the Market view. Coordinate
   the `Market.tsx` / `App.tsx` edit with codex.
5. **No look-ahead.** The endpoint reads only the requested `trade_date`'s partitions; never joins a
   later date. Defaults resolve to the latest date *with data*, like `health.py`.

## Test surface

Read `tasks/TESTING.md`. Independent oracles; expected values from a source other than the code
under test.

- **BFF per-expiry aggregation — independent oracle.** A fixture `instrument_master` with a known,
  hand-counted set of contracts (e.g. 3 expiries × {2,3,1} strikes, mixed C/P) → assert the endpoint
  returns the hand-counted `n_strikes`/`n_calls`/`n_puts`/`strike_min`/`strike_max` per expiry.
- **Per-tenor coverage reflects qc_results.** A fixture `qc_results` with a `tenor_coverage_floor`
  breach on `1m`/`3m` and a pass on `10d` → the endpoint surfaces `measured`/`floor`/`status` per
  tenor, and the empty tenors appear as `measured=0` rows (not omitted).
- **Empty / missing partition.** No `instrument_master` for the date → 200 with `n_expiries=0` and a
  labeled empty body; a bad `trade_date` string → 400 (mirror `health.py`).
- **Front component (component test).** Given a populated payload, the table renders one row per
  expiry and the per-tenor badges; given the empty payload, it renders the empty state — assert
  user-visible text/rows, not internal state.
- **No look-ahead.** A request for past date D ignores a later date's partitions (inject one; assert
  unchanged). `check-lookahead-bias` clean on the read path.
- Gate green: `uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q`
  **and** the web test suite.

## Done criteria

`GET /api/coverage` returns per-expiry capture counts + per-tenor QC coverage (whole grid, empty
tenors as zero-rows) + overall qc_status from already-persisted tables; a `CoverageTable` panel on
Tab 1 renders both sections with QC badges and a clean empty state; the captured-2026-06-11 gap
(8 June expiries, 1m…3y empty, QC red) is **visible at a glance**; no look-ahead; both gates green.

## Gotchas

- **Surface only, no recompute.** This reads `instrument_master` + `qc_results`; it must not re-run
  QC or pricing. If a number isn't already on disk, it doesn't belong in v1 (quote-completeness from
  `raw_market_events` is an explicit phase-2 add, not v1).
- **Show the holes, don't fill them.** Empty tenors are the whole point — render them as labeled
  zero-rows. Never default a missing tenor to a blank/omitted row (that is exactly how the bug
  hid). Mirror `serializers`' `has_holes` honesty (F-BFF-03).
- **One tenor map.** `surfaces.tenor_years` / `tenor_target_dates` is the single home; do not parse
  `"1m"` again in the BFF.
- **Shared-tree file claim.** New files are disjoint, but the `App.tsx`/`Market.tsx` placement edit
  overlaps the live front lane — claim and coordinate, don't cross.
- **uv only** for env/deps; the web side uses the existing front toolchain.
