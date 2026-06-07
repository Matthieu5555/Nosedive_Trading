# 2B — Stress & scenario page: ±50% spot / ±50% vol PnL surface on the basket

> **Phase 2, parallel-OK behind the front-first gate. This is the Friday 2026-06-12
> "stress-test page must be functional" deliverable.** The compute already exists — the
> `infra/risk` scenario engine reprices explicit shocked market states and the versioned
> grid is built from config (ADR 0006). 2B is **wiring plus a page**: drive that engine's
> grid from the 2A basket through the existing BFF risk seam, keep the ±50%/±50% grid
> **config-driven** (the shocks and steps live in `scenarios.yaml`, never a `.py` literal —
> ADR 0028), and render the PnL surface with Plotly (ADR 0030). The blueprint (ADR 0011)
> overrides this file on every shock convention, formula, and grid definition; where this
> file and the blueprint disagree, the blueprint wins.

- **Owns:** the ±50%/±50% stress grid wired onto the 2A basket and the page that renders
  its PnL surface. On the config side: the extended `configs/scenarios.yaml` stress block
  (the ±50% spot range, the ±50% vol range, and the step counts — all config, hashed into
  `config_hashes["scenarios"]`). On the Python BFF side
  (`apps/frontend/src/algotrading/frontend/`): the extension of the existing
  `routers/risk.py` `GET /api/risk/scenarios` seam so it can serve the basket's stress
  surface (the grid of `(spot_shock, vol_shock) → scenario_pnl` cells) for the 2A basket,
  plus any serializer reshaping into a surface payload. On the web side
  (`apps/frontend/web/src/`): the stress/scenario page and its Plotly 3D `surface` (or
  `mesh3d`) component, the typed `api.ts` client for the surface payload, and its
  registration. Conforms to **[ADR 0006](../.agent/decisions/0006-risk-engine.md)** (the
  scenario engine is the source of truth — full reprice, explicit shocked states),
  **[ADR 0028](../.agent/decisions/0028-configuration-and-reproducibility-standard.md)**
  (the grid is config, not literals), **[ADR 0029](../.agent/decisions/0029-contract-field-names-conform-to-blueprint.md)**
  (the cell field is `scenario_pnl`, never `pnl`/`cash_pnl`), and
  **[ADR 0030](../.agent/decisions/0030-frontend-visualization-and-ui-library-stack.md)**
  (Plotly `surface`/`mesh3d` for the 3D trace; shadcn/ui shell).
- **Depends on:** **2A** (`tasks/2A-*.md`) for the basket to stress — the multi-leg
  position set 2B reprices; without it there is nothing to shock. **`infra/risk`** (ADR
  0006, **already built**): `scenario_grid`, `full_reprice_pnl`, `scenario_line_pnls`,
  `scenario_totals`, `effective_scenario_version`, and `scenario_result` in
  `packages/infra/src/algotrading/infra/risk/scenarios.py`; the `ScenarioConfig`
  (`spot_shocks`/`vol_shocks`/`roll_down_days`) in
  `packages/core/src/algotrading/core/config/platform_config.py`; the `ScenarioResult`
  contract (`scenario_pnl`, `spot_shock`, `vol_shock`, `scenario_version`) in
  `packages/infra/src/algotrading/infra/contracts/tables.py`. The BFF seam
  (`routers/risk.py` `GET /api/risk/scenarios`, `scenario_result_to_dict`) is **real** and
  already serves persisted `scenario_results` cells — 2B extends it, it does not build a
  new engine or a new endpoint family. **ADR 0030** for the Plotly surface. The
  basket-variance primitive (`risk/basket.py`) and the front stack (Plotly + shadcn) that
  1I introduces are reused, not re-added.
- **Blocks:** **2C** (attribution): 2C reads the **same full-reprice cells** 2B drives, so
  the grid wiring and the cell payload 2B fixes are the seam 2C attributes over. Build the
  cell shape clean.
- **State going in (verified 2026-06-07):** the scenario engine reprices explicit shocked
  states (`spot_shock` relative — `new_spot = spot*(1+spot_shock)`; `vol_shock` additive —
  `new_vol = vol + vol_shock`; full reprice is the source of truth, the Taylor approximation
  is the fast convenience that diverges for large shocks) and the grid is a pure function of
  `ScenarioConfig`. `scenario_grid` builds **one scenario per listed shock** (a spot family,
  a vol family, one combined crash, a time roll) — it does **not** yet build the **full
  cartesian (spot × vol) surface** the page needs; that cross-product is part of the wiring.
  Today's `configs/scenarios.yaml` carries `spot_shocks: [-0.10..0.10]`, `vol_shocks:
  [-0.05..0.05]`, `roll_down_days: [1]` — **not** the ±50%/±50% range; the stress range and
  steps must be added there as config (no literal in `.py`). `routers/risk.py` exposes
  `GET /api/risk/scenarios` serving persisted `scenario_results` cells via
  `scenario_result_to_dict` (`scenario_pnl`/`spot_shock`/`vol_shock`). The **`/api/market`
  router and `store_serving.py` were deleted in C4** — do **not** cite them; the BFF reads
  the store read-only. The web app gains its Plotly/shadcn stack through 1I; 2B reuses it.

## Objective

An operator opens the stress page, sees the 2A basket's PnL plotted as a **3D surface over
the (spot-shock × vol-shock) grid** — spot shocked across **±50%** and implied vol shocked
across **±50%**, on a config-defined number of steps — and watches the surface move as the
basket changes. The center cell (0% spot, 0% vol) is ≈ 0 PnL by construction. Every cell is
a **full reprice** of the basket under that explicit shocked market state (ADR 0006), not a
Greek multiplier and not a Taylor approximation — so the rendered surface **matches a full
reprice on the scenario grid within tolerance** (the acceptance criterion). The ±50% ranges
and the step counts are **config** (`scenarios.yaml`, hashed into `config_hashes`), so a
range change is a YAML edit and re-renders the surface with no code change. The page
self-labels (axes, units, what shock convention each axis uses); the dollar PnL carries its
unit string. No look-ahead: the surface stresses the basket's current snapshot state only.

## What to do (ordered)

1. **Add the ±50%/±50% stress grid to `configs/scenarios.yaml`.** Extend the scenarios
   bundle with a named **stress-surface** block carrying the spot-shock range (±0.50), the
   vol-shock range (±0.50, additive in vol units per the engine convention), and the step
   count per axis — all as config values, hashed into `config_hashes["scenarios"]`. Mirror
   the field into `ScenarioConfig` (`platform_config.py`) with validation (range symmetric,
   steps positive, center 0 included) via the C7 `from_config` path — **no `.py` literal**
   for any range or step. The version label moves when the stress block changes (it feeds
   `effective_scenario_version`).
2. **Build the (spot × vol) surface grid in `infra/risk`.** Add a deterministic
   surface-grid builder beside `scenario_grid` that emits the **full cartesian product** of
   the spot-shock axis × the vol-shock axis from the new config (each cell a `Scenario` with
   that `(spot_shock, vol_shock)`, `time_shock = 0`), with stable ids and fixed ordering so
   the grid is a pure function of the config. Reuse `shock_valuation` + `full_reprice_pnl`
   /`scenario_line_pnls`/`scenario_totals` to reprice the basket lines per cell — **do not
   add a second reprice path**. The per-cell total is the basket's full-reprice PnL for that
   shocked state. Center cell (0,0) totals ≈ 0.
3. **Drive the grid from the 2A basket.** Take the 2A basket's position lines (the
   `PositionRisk` set 2A produces, or its persisted `scenario_results` for the basket
   portfolio) and run the surface grid over them. Persist via `scenario_result` into the
   `scenario_results` contract (`scenario_pnl` per cell, `spot_shock`/`vol_shock` per axis,
   the `effective_scenario_version`) so the page reads the store read-only, like every other
   panel — the EOD/compute path writes, the BFF never computes.
4. **Extend the BFF `/api/risk/scenarios` seam for the surface.** In `routers/risk.py`,
   serve the basket's stress cells reshaped into a **surface payload**: the spot-shock axis,
   the vol-shock axis, and the `scenario_pnl` z-grid aligned to those axes (plus the
   `scenario_version` and provenance), so the front renders a Plotly `surface` directly.
   Keep the existing cell-list response intact (2C reads cells); the surface is an additive
   shape over the same `scenario_results` rows. Field names follow ADR 0029
   (`scenario_pnl`, `spot_shock`, `vol_shock`) — do not invent `pnl`/`z`. A missing/empty
   basket partition returns a labeled empty surface (empty axes), never a 500 (match the
   surfaces router's missing-partition behaviour).
5. **Typed client (web).** Extend `api.ts` with a `StressSurfaceResponse` interface
   mirroring the new payload (spot axis, vol axis, `scenario_pnl` z-grid, version, unit
   string). Reuse the existing `getJson`/`useFetch`/`AsyncBlock` helpers; the HTTP shape is
   the seam (keep serializer and `api.ts` in lockstep).
6. **The stress page (web).** Add the page rendering the Plotly **3D `surface`** (or
   `mesh3d`) of `scenario_pnl` over (spot-shock %, vol-shock %), axes labeled with the shock
   conventions and the PnL unit string, self-describing panel label. Register the page/route
   in the web app shell (shadcn). A labeled empty state when the basket has no cells. No
   second charting dependency — Plotly only (ADR 0030).

## Test surface

Read [TESTING.md](TESTING.md). The independent-oracle, determinism, edge-case, and seam
rules there are mandatory; the cases specific to 2B:

- `test_stress_surface_matches_full_reprice` — **the acceptance criterion, independent
  oracle.** For a small hand-built basket, compute each cell's PnL by an **independent full
  reprice** (reprice the basket at `new_spot = spot*(1+s)`, `new_vol = vol+v` with the
  pricing engine directly in the test, difference against base) and assert the surface
  payload's `scenario_pnl` z-grid equals it within tolerance. The center cell (0,0) is ≈ 0.
- `test_stress_grid_is_the_full_cartesian_product` — the surface grid has exactly
  `n_spot_steps × n_vol_steps` cells, every `(spot_shock, vol_shock)` pair present once, in
  the fixed order; no missing or duplicated cells (cartesian completeness).
- `test_stress_range_is_config_driven` — the ±50% ranges and the step counts come from
  `scenarios.yaml`/`ScenarioConfig`; changing the YAML range/steps changes the axes and the
  cell count with **no code edit**; a grep guard asserts no ±0.50 / step literal lives in a
  `.py` file (ADR 0028).
- `test_shock_conventions_hold` — `spot_shock` is relative (`new_spot = spot*(1+s)`),
  `vol_shock` is additive (`new_vol = vol+v`); a sign flip on the spot axis moves the PnL in
  the expected direction for a known long-call basket.
- `test_scenario_version_moves_with_stress_block` — editing the stress range/steps moves
  `effective_scenario_version` (it folds the grid-construction hash), so a surface
  regenerates exactly from basket + snapshot + version; cross-process hash stability of the
  version (subprocess, no `PYTHONHASHSEED` reliance).
- `test_surface_payload_uses_blueprint_field_names` — the BFF surface payload uses
  `scenario_pnl`/`spot_shock`/`vol_shock` (ADR 0029); a renamed contract field turns the
  assertion red.
- **BFF↔infra seam (extend `apps/frontend/tests/test_readback_api.py`):** seed real
  `scenario_results` rows for a basket portfolio through `ParquetStore.write`
  (hand-chosen, internally-consistent values derived independently of BFF output) and assert
  `/api/risk/scenarios` reshapes **those** cells into the surface grid unchanged with
  provenance (`test_stress_surface_reads_back_basket_cells`); the existing cell-list shape
  still round-trips (2C's read).
- `test_empty_basket_is_labeled_empty_not_500` — an unknown/empty basket portfolio returns
  a labeled empty surface (empty axes), HTTP 200, never a 500.
- **No look-ahead:** the surface stresses only the basket's snapshot state; injecting a
  later snapshot does not change a cell (run the `check-lookahead-bias` skill over the
  grid-drive path).
- **Edge cases (the floor):** empty basket (empty surface), single-leg basket (a valid
  surface), a degenerate range (zero width → the center column only, labeled), the center
  cell exactly at (0,0), NaN/inf shock or vol rejected with a structured diagnostic.
- **Web component test (Vitest + Testing Library, alongside `Risk.test.tsx`):** the page
  renders the Plotly surface trace from a mocked `/api/risk/scenarios` surface payload, the
  axes carry their shock-convention labels, the PnL unit string is visible, and a fetch
  error renders through `AsyncBlock` (not a blank page). Mock the endpoint; do not hit a
  live BFF.
- Branch coverage on the new surface-grid builder at or above the committed floor (≥90%).
- Both gates green: `uv run ruff … && uv run mypy … && uv run lint-imports && uv run pytest`
  for the Python side; `npm run lint && npm test` in `apps/frontend/web` for the page.

## Done criteria

The 2A basket's PnL renders as a Plotly 3D surface over the **±50% spot × ±50% vol** grid;
every cell is a full reprice (ADR 0006) and the rendered surface **matches an independent
full reprice on the scenario grid within tolerance**; the ranges and steps are
config-driven in `scenarios.yaml` (hashed, no `.py` literal — ADR 0028) and a range edit
re-renders with no code change; the grid is driven through the **existing**
`GET /api/risk/scenarios` seam (extended, not replaced) reading `scenario_results` read-only;
the cell field is `scenario_pnl` (ADR 0029); the page self-labels axes and the PnL unit; no
look-ahead; the cell payload is clean for 2C to attribute over; both gates green
(uv for Python, npm for web). This is functional by Friday 2026-06-12.

## Gotchas

- **Blueprint (ADR 0011) overrides** every shock convention, formula, and grid definition
  here. If the blueprint's stress definition differs (e.g. multiplicative vs additive vol,
  a different ±range), follow it and note the divergence — do not encode this file's ±50%
  defaults over it.
- **Full reprice is the source of truth, not Taylor (ADR 0006).** At ±50% the local Greeks
  approximation diverges badly — that is expected and is exactly why the surface must be the
  full reprice. The acceptance test compares to a full reprice, never to the Taylor path;
  do not let `local_approx_pnl` leak into the rendered z-grid.
- **The compute exists — do not build a second engine.** Reuse `shock_valuation` +
  `full_reprice_pnl` / `scenario_line_pnls`; the new code is the cartesian **grid builder**
  and the **wiring**, not a new repricer. A forked reprice path is the failure mode this
  task exists to avoid.
- **Config, not literals (ADR 0028).** The ±50% ranges and the step counts live in
  `scenarios.yaml` and enter `config_hashes["scenarios"]`; nothing economic is a `.py`
  literal. The grep guard in the tests enforces it.
- **`scenario_pnl`, never `pnl`/`cash_pnl` (ADR 0029).** The contract field and the payload
  key are `scenario_pnl`; `spot_shock`/`vol_shock` are the axes. A renamed field must break
  the seam test.
- **Extend the seam, don't fork it.** `GET /api/risk/scenarios` already serves cells; add
  the surface shape over the same `scenario_results` rows. 2C reads the cells — keep that
  response intact so 2C and 2B share one source of truth (2C attributes the same reprice).
- **Serving is read-only; the cron is the sole writer** (ADR 0034 §1). The BFF reshapes
  persisted cells into a surface; it does **not** run the reprice on request. The compute
  path writes `scenario_results`; the page reads them back.
- **Do not resurrect deleted code.** `/api/market` and `store_serving.py` were removed in
  C4. If the basket's cells are absent, return a labeled empty surface — never synthesize.
- **Plotly only (ADR 0030).** The 3D trace is Plotly `surface`/`mesh3d`; no second charting
  dependency, no ECharts-GL for the one surface.
- **uv** for every Python command (tests, gate); **npm** for the web page. No bare
  `python`/`pip`.
