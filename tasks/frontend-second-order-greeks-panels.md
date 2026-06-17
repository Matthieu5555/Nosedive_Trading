# frontend-second-order-greeks-panels — carry Vanna/Volga/Charm through to the front panels

> **Re-scoped & pickable (owner ruling, 2026-06-17 — these Greeks must reach the front).** The
> page-1 rebuild (`c4ce734`) deleted the old render targets (`DollarGreeks`, `GreeksTermStructure`);
> the second-order Greeks now belong on the **3-onglets homes**
> ([frontend-3onglets-target-ux](frontend-3onglets-target-ux.md)): the **Onglet 1 › ③ Panneau Ténor**
> Greeks table + shape-curves block (`:45-47`), and the **Onglet 2 › ④ Attribution** panel
> (Rho/Vanna/Volga terms beside Δ/Γ/Vega/Θ, `:72-73`). Compute (Vanna/Volga/Charm, raw+cash) is
> **landed on `main`**; this slice is the wiring that carries it through to those two panels so it
> stops being banked-but-invisible. Sequence under the single 3-onglets front owner (shared
> `serializers.py`/`api.ts`).

> **Front slice (step 3) of [infra-second-order-greeks](archive/infra-second-order-greeks.md) (landed/archived 2026-06-14).**
> Steps 1-2 (compute) landed: Vanna/Volga/Charm in `black76` / `dollar_greeks` /
> `PricingResult` (raw + cash + unit strings), and attribution carrying Rho/Vanna/Volga +
> realized day-over-day. Step 3 was deferred and explicitly marked "owned elsewhere" to
> avoid colliding with the 3A ticket lane: carry the new Greeks/terms through
> `serializers.py → api.ts → front panels`. This is that step.

## Why (TARGET cite)
TARGET §5.1 (Greeks in natural units **and** dollars, including the second-order set) and
§5.2 (attribution drillable per term). Course transcript req #3/#8. The pricing layer now
**emits** these columns but the pricing serializer lists fields explicitly, so the new
contract columns are **inert** until the BFF serializer and `api.ts` contract surface them
and the front term-structure / attribution panels render them. Until then the compute is
banked but never reaches the operator's screen — the exact "did it reach the screen,
correctly labelled, in dollars" gap this layer owns.

## Scope boundary
- **In:** extend `apps/frontend/src/algotrading/frontend/serializers.py` to surface
  Vanna/Volga/Charm (raw + cash) with their `UNIT_STRINGS` / `charm_unit_string`; extend
  the `api.ts` typed contract to match; render them on the **Onglet 1 › ③ Panneau Ténor** Greeks
  table + shape-curves block (the per-tenor Greeks home in the reading model) and add the
  Rho/Vanna/Volga terms to the **Onglet 2 › ④ Attribution** panel beside the existing Δ/Γ/Vega/Θ.
  Each value self-labelled with its unit string; small magnitudes respect the sig-fig formatter
  (coordinate with `frontend-sigfig-scientific-display`).
- **Out:** the pricing/attribution math (landed) — never re-implement a Greek or a
  $-conversion in the BFF; the serializer reads the cash values the compute layer already
  produced. Charm is a **display** Greek, not an attribution term (the dPnL eq stops at
  Volga) — render it on the Greek panels, not in the attribution decomposition.

## Dependencies / coordination
- Reads the landed `PricingResult` columns + `ScenarioAttribution` seam from
  infra-second-order-greeks (compute steps 1-2, on main).
- **Sequence after the 3A ticket lane and `frontend-sigfig-scientific-display` merge** —
  the parent flagged the serializer/`api.ts` edit as a collision risk with the 3A lane; do
  it once both have landed. Coordinate the `format.ts` touch with
  `T-front-currency-and-bands` (vincent) and the sig-fig task — shared-tree hazard.

## Done criteria
Vanna/Volga/Charm (raw + cash, unit-tagged) render on the Onglet 1 ③ Panneau Ténor Greeks block;
Rho/Vanna/Volga attribution terms render in the Onglet 2 ④ Attribution panel; small Greeks keep
their sig-figs; no math re-implemented in the BFF; web gate green and the Python BFF tests green.
