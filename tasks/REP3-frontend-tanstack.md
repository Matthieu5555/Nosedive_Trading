# REP3 — Frontend: adopt TanStack Query/Table, fix the 3D surface

> **READY — no blocker.**
> ([AUDIT-library-leverage-2026-06-07.md](AUDIT-library-leverage-2026-06-07.md))
> The web shell hand-rolls fetch/cache/polling that TanStack Query owns (and that the ADR
> already framed TanStack as the vendor for), bypasses the installed TanStack Table on two
> grids, and renders the 3D surface with a real readability bug.

- **Owns:** `apps/frontend/web/` — `hooks/useFetch.ts`, `components/AsyncBlock.tsx`,
  `src/api.ts` (get/postJson), `pages/Run.tsx`, `pages/Surfaces.tsx`, `pages/Risk.tsx`,
  `components/DollarGreeks.tsx`, `components/charts.tsx`; trivial dedup in the BFF
  `apps/frontend/src/algotrading/frontend/serializers.py`.
- **Depends on:** nothing. (Verifies with `npm run lint && npm test` per AGENTS.md.)
- **Blocks:** nothing, but should land **before** Phase 2's Tab-2 grids (2A/2C/2D) multiply
  the hand-rolled pattern.
- **State going in:** `@tanstack/react-table` is installed and used by `ConstituentTable`;
  `@tanstack/react-query` is **not installed** despite [ADR 0030](../.agent/decisions/0030-frontend-visualization-and-ui-library-stack.md)
  framing TanStack as a chosen vendor. plotly is the right single charting dep.

## Objective

Make the web shell lean on the sanctioned libraries it already implies, and fix the chart
panels so they render what the prof actually asked for: the **fitted** vol surface/smile (not
a raw point cloud) and the Greeks in **both** representations side by side (decimal + dollar),
per the course transcript and [ADR 0036](../.agent/decisions/0036-dollar-greek-units-and-monetization-conventions.md).

## What to do (ordered)

1. **Install `@tanstack/react-query`**; replace `hooks/useFetch.ts`,
   `components/AsyncBlock.tsx`, and `api.ts:187-211` get/postJson with `useQuery`/`useMutation`.
   Map the keyed cache onto the index→date→ticker cascade (`Home.tsx`).
2. **`pages/Run.tsx:25-34` `setTimeout` poll → `refetchInterval` + `enabled` gating.** This
   also fixes a real bug: the current loop fires after navigation away (no cancel-on-unmount).
3. **Extend the `ConstituentTable` TanStack-Table pattern** to the two plain `<table>`s in
   `pages/Surfaces.tsx:29-59` and `pages/Risk.tsx:16-41` (and `DollarGreeks.tsx`) — these are
   dense SVI-param / net-Greek grids, the lib's sweet spot, and get sorting for free.
4. **`components/charts.tsx:58-77` `VolSurface`: `mesh3d`-of-point-cloud → plotly `surface`.**
   Today it flattens the observed band points into a sparse triangulated cloud (jagged —
   defeating ADR 0030's "replace the unreadable 2D view" goal). Render a **smooth gridded
   surface by sampling the fitted SVI over a dense regular (Δ-band × maturity) grid** — the
   BFF already serves the per-maturity SVI params (`surface_slice` / `surface_parameters_to_dict`:
   `svi_a/b/rho/m/sigma`); the served `points`/`smile` are sparse, so sample the curve client-side
   (or have the analytics router emit a dense grid). Overlay the solved/observed points with
   `scatter3d` (mirror `scripts/plot_live_surface.py:108-129`).
5. **`components/charts.tsx` `SmileChart`: overlay the fitted SVI curve on the observed points.**
   Today it draws only the observed band points (`maturity.smile.deltas/implied_vols`). The prof
   wants the **fitted parametric form** ("trouver un formulaire"), not just dots: add a second
   trace — the SVI curve sampled densely from the same `surface_slice` params — over the observed
   scatter, so smile-fit quality is visible. A maturity with no `surface_slice` falls back to the
   points-only view (labelled).
6. **`components/DollarGreeks.tsx` → decimal + dollar co-equal.** The component is named
   "Dollar Greeks" and shows `raw` as the last column under a $-first caption — reads as
   dollar-only. The transcript ruling is **both, side by side** (front-end review: *"les Grecs,
   on a dit en dollars … ce ne vaut pas [la peine de] les supprimer"*; roadmap: *"two
   representations side by side — decimal and dollar"*; ADR 0036: raw per-unit is the source of
   truth, dollar derived). Rename to `GreeksPanel` (or `Greeks`) and present **Greek · decimal
   (raw) · $ value · unit** with the decimal column first as the source of truth. Data-model is
   unchanged — `{raw, dollar, unit}` already carries both; this is presentation only. Update
   `Home.test.tsx`/component tests to assert the **decimal** value is visible, not just the unit
   string.
7. **Trivial:** dedup `serializers.py:150-157` / `188-196` (`_metric` / `_analytics_metric`
   are byte-identical) into one helper.

## Done when

`npm run lint && npm test` green; no `useFetch`/`AsyncBlock`/manual-poll left; the three
grids share the TanStack-Table pattern; the 3D view renders a **smooth fitted gridded
surface** and the 2D smile shows the **fitted SVI curve over the observed points**; the
Greeks panel shows **decimal and dollar co-equally** (decimal first), asserted by a component
test; web fixtures (`web/src/test/fixtures.ts`) still pass. (Coordinate with REP4 on whether
the UI primitives move to shadcn at the same time.)
