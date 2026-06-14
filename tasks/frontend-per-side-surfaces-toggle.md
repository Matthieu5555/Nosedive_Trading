# frontend-per-side-surfaces-toggle — surface_side toggle on the 3D surface + smiles (R2 front half)

> **Source:** TARGET §4 **R2** + §7 #6; the front half of [infra-per-side-surfaces](archive/infra-per-side-surfaces.md),
> which landed the infra core (per-side fit, `surface_side` in the grid contract, put−call IV
> spread signal + QC, ADR 0048) 2026-06-14. Split out the way the second-order-greeks front work
> was split from its infra lane.

## The gap
The projected grid now carries `surface_side ∈ {put, call, combined}` (up to three rows per
`(tenor, delta_band)` cell at the same strike). The whole BFF + web stack still reads
**combined only** — the analytics router filters to `surface_side == "combined"`
(`apps/frontend/src/algotrading/frontend/routers/analytics.py`), so the per-side put/call IVs
and the put−call spread the infra core computes are invisible to the operator.

## Scope (the front half only)
- **BFF:** carry `surface_side` through the analytics serializer + router; expose the put/call
  rows and the put−call IV spread (derive via `surfaces.put_call_iv_spread`, or read the put/call
  grid rows) per `(tenor, strike)`.
- **Web:** a **side toggle** (put / call / combined) on the 3D surface and the smile cards; a
  put−call IV-spread view (the R2 "makes money not just plots" deliverable — persistent spread =
  funding/skew signal, blowout = bad data). Keep vitest + the Playwright e2e green; extend both
  for the toggle.
- Combined stays the default view, so nothing regresses for an operator who ignores the toggle.

## Out of scope
- The infra fit / contract / QC — **landed** (ADR 0048).
- Persisting per-side SVI params (`SurfaceParameters`/`SurfaceGrid` per side): still deferred —
  pick it up here only if the 3D per-side surface trace needs raw params rather than the grid.

## Done criteria
`surface_side` through BFF → web; a working put/call/combined toggle on the 3D surface + smiles;
a put−call IV-spread view; vitest + e2e green.
