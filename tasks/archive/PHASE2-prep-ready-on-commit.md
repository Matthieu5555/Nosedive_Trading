# Phase 2 — execute-on-commit prep (read-only, 2026-06-07)

**Why this file exists.** An implementing agent has Phase 0 + much of Phase 1 advanced
**uncommitted on the shared `main` tree**. The owner ruling today is *do not touch the shared
tree until it commits* (`tasks/AUDIT-tasks-coherence-2026-06-07.md` → UPDATE). Phase 2's compute
(`infra/risk` scenario engine, ADR 0006) and the `$`-Greek layer (`infra/pricing/dollar_greeks.py`,
ADR 0036) **already exist on disk**, so Phase 2 is wiring + UI — parallelisable now, except the
shared-tree freeze. This file is the read-only prep so that the moment the agent commits, 2A→2D are
**execution, not investigation**. Nothing here edits a task spec or any code (those edits are
themselves deferred to after the commit — bucket B of the audit).

The Phase-2 specs (`2A`–`2D`) are still good on *intent* but **stale on two pointers** the agent's
work moved. Read them through the corrections below.

---

## The two corrections every Phase-2 task inherits

1. **The `$`-Greek home moved.** Specs say "the dollar-Greek math has one home: `1F` /
   `risk/greeks.py`". **It is now `packages/infra/src/algotrading/infra/pricing/dollar_greeks.py`**
   (ADR **0036**, not 0035 — 0035 became the index registry). That module is the single home of the
   five conversions and the two convention forks:
   - `dollar_delta  = Δ·S·mult·qty`                              — *$ per $1 of underlying*
   - `dollar_gamma  = Γ·S²·mult·qty / 100` (`one_pct`, default)  — *$ per 1% move*
                     `= Γ·S²·mult·qty`       (`one_dollar`)      — *$ per $1 move*
   - `dollar_vega   = vega·0.01·mult·qty`                        — *$ per 1 vol point*
   - `dollar_theta  = θ·mult·qty / day_count` (365 default)      — *$ per calendar day*
   - `dollar_rho    = rho·0.01·mult·qty`                         — *$ per 1% rate*

   Forks come from `MonetizationConfig` (`core.config`, `platform_config.py:457`):
   `gamma_normalisation="one_pct"`, `theta_day_count=365`. Unit strings live in
   `dollar_greeks.UNIT_STRINGS`; the typed result is `DollarGreeks` (frozen, slotted, carries
   `gamma_unit`/`theta_unit`). The BFF already serves these via `routers/risk.py::get_metrics`
   ("ADR 0036" cited in its docstring) → `serializers.pricing_result_to_dict`.

2. **P0.2 is done — all five `$`-Greeks exist.** 2A's "`PricingResult` carries
   `dollar_delta/gamma/vega` only; `dollar_theta`/`dollar_rho` arrive later, do not assume them"
   caveat is **resolved**. Sequence-after-P0.2 gating in 2A/2B no longer applies.

ADR renumber, everywhere the specs say it: **OQ-1 $-Greeks = ADR 0036**, **futures-deferred =
ADR 0037**. "ADR 0035" in `2A:25,29` (and any 2B/2C/2D echo) is stale → 0036.

---

## ⚠ The one correctness hazard to verify in code on commit (audit bucket C / item ~9-on-units)

**Unit-mixing across the basket sum.** 2A/2D sum the `$`-Greeks carried on 1F's
`ProjectedOptionAnalytics` rows — those are **per-1% / per-365** (the `dollar_greeks.py` analytics
convention). But `PositionRisk.dollar_*` in `infra/risk/greeks.py` is the **per-$1 / no-365** legacy
layer (unchanged — `scenarios.py` still imports `PositionRisk` from `.greeks`). If a basket sums
`ProjectedOptionAnalytics.dollar_gamma` (÷100) **and** `PositionRisk.dollar_gamma` (no ÷100) in the
same total, the number is silently wrong by 100×.

**First action after the commit:** grep both call paths and confirm 2A/2C/2D read **one** source.
The spec rule ("price by summation off 1F, never recompute") is right; the risk is two `dollar_*`
homes with different normalisation. Pin it with the 2A independent-oracle test
(`test_basket_dollar_greeks_equal_sum_of_leg_analytics`) computing the hand-sum in the **per-1% /
per-365** convention.

---

## 2A — basket builder (the contract 2B/2C/2D all sit on — build first)

Spec `tasks/2A-basket-builder.md` is accurate except the `$`-home pointer above. Ready-to-go map:

- **Contract:** add `BasketLeg` + `Basket` (frozen, slotted) to `contracts/tables.py`; register in
  `contracts/registry.py` via the **additive** schema path with a round-trip. Reuse — do not fork —
  `infra/risk/positions.py` `Position`/`PositionSet`. Side↔sign consistency enforced in
  `__post_init__` (long+negative qty = rejected structured error; zero qty rejected).
- **Pure risk fn:** new module **`infra/risk/multileg.py`** (NOT `risk/basket.py` — that is the
  index-variance identity Eq 23, `BasketVarianceResult`; do not overload). `(Basket, matching
  ProjectedOptionAnalytics rows + spot for stock legs, MonetizationConfig) → typed basket-risk
  result`. Basket `$`-Greek = `Σ signed_qty · row.dollar_<greek>`, reusing `aggregation.py`
  order-free summation. Keep **per-leg contributions** beside the aggregate (2C needs them).
  Unmatched `contract_key` → **labeled gap** (structured diagnostic, missing key), never 0/NaN.
- **BFF:** `routers/basket.py`, registered in `app.py` beside `health/surfaces/risk/run/config/oauth`.
  Read-only `ParquetStore`. Labeled gap → HTTP 200; malformed basket → 400 (mirror surfaces router's
  `bad_trade_date`). Serializer carries each `$` number with its `dollar_greeks.UNIT_STRINGS` string;
  ADR-0029 names (`dollar_*`/`forward_price`/`implied_vol`/`log_moneyness`).
- **Web:** TanStack-Table leg grid + one-click templates (straddle/strangle/risk-reversal) + live
  book-additive basket-risk panel with per-leg contributions. Reuse `getJson`/`useFetch`/`AsyncBlock`
  + `api.ts` typed-client pattern. Web stack (Plotly/shadcn/TanStack) lands with 1I — **consume, do
  not re-add**.
- **Tests:** the 2A list in the spec is correct; just compute the hand-sum oracle in the **per-1% /
  per-365** convention (see hazard above). Extend `apps/frontend/tests/test_readback_api.py` for the
  BFF↔infra seam.

## 2B — stress surface (Friday 2026-06-12 deliverable)

Spec `tasks/2B-stress-scenario.md` is accurate. The genuinely-unbuilt piece confirmed by reading
`infra/risk/scenarios.py`: **there is no cartesian (spot × vol) surface builder** — `scenario_grid`
emits a spot *family* + a vol *family* + one combined crash + a time roll, **not** the full grid.

- **Config:** add a `stress-surface` block to `configs/scenarios.yaml` (±0.50 spot, ±0.50 vol
  additive, step counts) → mirror into `ScenarioConfig` (`platform_config.py`) with validation
  (symmetric range, steps>0, center 0 present) via the C7 `from_config` path. **No `.py` literal**
  (ADR 0028; the spec's grep guard enforces it). Today's file is ±0.10/±0.05 — confirm post-commit.
- **Grid builder:** add a cartesian builder **beside** `scenario_grid` (each cell a `Scenario` with
  `(spot_shock, vol_shock)`, `time_shock=0`), reusing `shock_valuation` + `full_reprice_pnl` /
  `scenario_line_pnls` / `scenario_totals` — **do not add a second reprice path**. Persist via
  `scenario_result` into `scenario_results`. Center (0,0) ≈ 0.
- **BFF:** extend `routers/risk.py` `GET /api/risk/scenarios` with an **additive** surface payload
  (spot axis, vol axis, `scenario_pnl` z-grid + `scenario_version` + provenance) over the same
  `scenario_results` rows. Keep the existing cell-list response intact (2C reads it). Empty basket →
  labeled empty surface, 200.
- **Web:** Plotly `surface`/`mesh3d` page, axes labelled with shock conventions + PnL unit string.
- **Acceptance:** `test_stress_surface_matches_full_reprice` — surface z-grid == an independent full
  reprice in the test, within tolerance. **Never** compare to the Taylor path (`local_approx_pnl`):
  at ±50% it diverges by design.

## 2C — PnL attribution (audit items #11, #15 — spec needs two reframings)

- **#11 — terms come from the Taylor expansion with the *scenario shocks*, not from `greeks.py`'s
  `dollar_*` values.** 2C's invariant `sum(terms) == local_approx_pnl` matches `_taylor_pnl`
  (`scenarios.py:197-206`): `Δ·dS + ½Γ·dS² + vega·dvol + θ·dt`. Derive the per-Greek terms from those
  shocked deltas; the ADR-0029 requirement on 2C is the **naming**, not the per-$1 numeric
  convention. (Spec `2C:48-49,184-186` currently names `greeks.py`'s `dollar_*` as the basis — wrong
  basis, right names.)
- **#15 — 2C owns its attribution-waterfall web component.** The spec delegates the React/Plotly to
  1I, but 1I scopes itself out of Tab-2 pages and 2A/2B/2D each own their UI. 2C is the lone outlier
  — it should own its waterfall component, reusing 1I's stack.
- New attribution seam: `ScenarioResult` carries only `scenario_pnl` (one number), so per-Greek
  terms need a new payload shape (`2C:106-109`) — additive over the same cells 2B drives.

## 2D — strategy composition (audit item #4 — spec premise is false)

2D is written four times (`2D:24,27,42,204`) on the premise that 2A/2B/2C "have no task files yet"
and tells the implementer to invent their contracts. **They exist** (this dir). On commit, 2D's
dependency section must be rewritten to cite the concrete seams: 2A's `Basket`/`BasketLeg` +
`infra/risk/multileg.py`, 2B's surface grid, 2C's attribution shape. Also: 2D cites the "C7 DI
pattern" — C7 is archived; point to **ADR 0028** instead (`2D:102`). And 2D must consume
`pricing/dollar_greeks.py` (per-1%/per-365), not `greeks.py` — same hazard as above.

---

## Sequencing (unchanged from the roadmap, restated for Phase 2)

`2A` (freezes the basket contract) → then `2B` ∥ `2C` ∥ `2D` are wiring on it. 2B is the Friday
stress-page deliverable; build the 2A cell/contract shape clean so the others don't rework it.

## Execute-on-commit checklist

1. `git log --oneline -3` confirms the agent committed; working tree clean for the Phase-2 files.
2. Re-audit **code** bucket C: confirm 1F/2A consume `dollar_greeks.py` (per-1%/per-365)
   consistently; confirm SP500-vs-SPX index symbol is consistent; confirm `actor/close_capture.py`
   honours as-of (`check-lookahead-bias`).
3. Apply the bucket-B spec edits (this file's corrections) to `2A`–`2D` so the specs match code.
4. Build 2A (contract + `multileg.py` + `routers/basket.py` + web), root gate + web gate green.
5. Build 2B on the 2A contract → the Friday stress page.

**Pointers:** engine `infra/risk/scenarios.py`; `$`-Greeks `infra/pricing/dollar_greeks.py` (ADR
0036); config `MonetizationConfig`/`ScenarioConfig` in `core/config/platform_config.py`; BFF
`apps/frontend/src/algotrading/frontend/routers/risk.py` + `serializers.py`; audit
`tasks/AUDIT-tasks-coherence-2026-06-07.md`.
</content>
</invoke>
