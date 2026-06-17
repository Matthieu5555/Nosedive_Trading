# Task — Consolidate the front to 3 onglets (Données / Risque / Ordres)

**Status:** open — **P0** (2026-06-17). **Land by Friday 2026-06-19.**
**Lane:** `frontend-` (web + a little BFF). **Design contract:** [`frontend-3onglets-target-ux.md`](frontend-3onglets-target-ux.md)
(owner-locked, blueprint + transcript grounded) — implement it; do not re-decide the UX.

> **⚠️ ONE front owner, SEQUENTIAL steps — do NOT parallelize.** Every step touches the shared shell
> (`routes.ts`, `App.tsx`) and shared components. A single agent runs steps 1→4 in order. Ground every
> choice in the design contract + `docs/blueprint/` + `docs/transcripts/`; do not revert recent owner
> decisions (ADR 0051, the 3-onglets target UX, the hygiene). The shell flip (step 4) comes **last**,
> after the pages it references exist.

## Step 1 — Onglet 1 (Données) v2  *(depends on `frontend-bff-bidask-volume` for the price block)*
Bring the Market page to the locked reading model (`…target-ux.md` §Onglet 1):
- **Scorecards → a thin strip at the very top**, using the **design-system theme** (legible — fix the
  white-bg illegibility). 4 numbers: ATM · skew 25Δ · convexity · RV−IV.
- **Restore the master-detail constituents** (weighted list + **selected member's candlestick** = the
  2nd candlestick, default heaviest) and **move it up** into the price block (resurrect the
  `vincent`-remote `ConstituentsWorkspace`; `git show vincent:apps/frontend/web/src/pages/market/ConstituentsWorkspace.tsx`).
- **Greeks shape curves** vs strike (gamma/vega bell, delta S) added beside the greeks table in the
  tenor panel (option c — complementary, not redundant).
- **Price-structure block** in the tenor panel: per strike → **bid / ask / volume** (from the BFF task).
- Files: `pages/Market.tsx`, `pages/market/*`, `components/charts.tsx`, the Scorecards component.
- Gate: web lint + vitest + tsc green.

## Step 2 — Onglet 2 (Risque): fold Risk + Positions into the basket tab
Per `…target-ux.md` §Onglet 2 (compose → see → shock → explain):
- The book view (**Positions**: summary $Greeks, open legs, fills ledger) becomes the "② Le book"
  section of the basket tab. Named historical scenarios (from RiskScenarios) become **shock presets**
  in the "③ Choquer" step. Attribution stays. Broker reconciliation **leaves** for Onglet 3.
- Retire the standalone `RiskScenarios.tsx` and `Positions.tsx` as tabs (content moved in).
- Files: `pages/Basket.tsx` (→ the Risque tab) + `pages/basket/*`, fold `RiskScenarios.tsx`/`Positions.tsx`.
- Gate green.

## Step 3 — Onglet 3 (Ordres): new tab
Per `…target-ux.md` §Onglet 3 (ticket → gated paper send → recon → backtest):
- New Ordres page: move the `TicketPanel` (from Basket) here; fold the **Strategy** page (backtest)
  here; move **broker reconciliation** here. Keep the live-send disarmed (3B gated, security M2).
- Files: new `pages/Ordres.tsx` (+ subcomponents), move `TicketPanel`, fold `Strategy.tsx` + `reconciliation`.
- Gate green.

## Step 4 — Shell / nav: 7 tabs → 3  *(LAST)*
- `routes.ts` + `App.tsx`: nav = **Données → Risque → Ordres** (3 tabs). Drop the **Signals** tab
  (its content lives in Onglet-1 scorecards + ρ̄). Demote **Operations** to a secondary utility (not a
  top-level tab). Rename Market→Données, Basket→Risque; add Ordres. Update redirects + e2e nav specs.
- Files: `routes.ts`, `App.tsx`, `App.test.tsx`, `e2e/navigation.spec.ts`.
- Gate: web lint + vitest + tsc + e2e green.

## Acceptance (whole consolidation)
- The app shows exactly **3 top-level tabs** (Données / Risque / Ordres); Signals gone, Operations
  secondary. Each tab matches its locked reading model in `…target-ux.md`. No constituent-as-option
  -underlying anywhere (ADR 0051). Full web gate + e2e green at land.
