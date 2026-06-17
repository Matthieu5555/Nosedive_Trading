# frontend-per-side-surfaces-toggle — surface_side toggle on the 3D surface + smiles (R2 front half)

> **🗄️ RETIRED (2026-06-17 board audit) — the toggle deliverable was superseded by a shipped design
> decision.** The web no longer has (and deliberately rejects) a put/call switch: `charts.tsx:200-205`
> superimposes the put and call smiles side-agnostically ("the page no longer has a put/call switch —
> the asymmetry is the point"), so the **put−call gap is already visible as the wing spread** without a
> toggle. The infra core (per-side fit, `surface_side` grid PK, put−call IV spread + QC, ADR 0048)
> remains landed and correct; only this *front toggle* framing is dead. The BFF still serves combined
> only (`analytics.py:37` filters `surface_side != combined`). **If** the owner later wants the raw
> per-side / IV-spread *payload* exposed (distinct from the visual overlay already shipped), open a
> fresh, narrower spec for that BFF slice — do not resurrect the toggle. Archived alongside the landed
> [infra-per-side-surfaces](infra-per-side-surfaces.md).

> **Source:** TARGET §4 **R2** + §7 #6; the front half of [infra-per-side-surfaces](archive/infra-per-side-surfaces.md),
> which landed the infra core (per-side fit, `surface_side` in the grid contract, put−call IV
> spread signal + QC, ADR 0048) 2026-06-14. Split out the way the second-order-greeks front work
> was split from its infra lane.
>
> **3-onglets home (2026-06-17):** the side toggle lives on **Onglet 1 (Données) › ② NAPPE 3D +
> ③ put/call smile** ([frontend-3onglets-target-ux](frontend-3onglets-target-ux.md):37,45), not a
> standalone surface page; the put−call IV-spread view is a diagnostic in the same tenor block.

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
