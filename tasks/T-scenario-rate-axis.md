# T-scenario-rate-axis — scenarios.yaml has no rate-shock axis the course prescribed

> **From the 2026-06-12 intent-vs-delivery audit** ([report](AUDIT-INTENT-VS-DELIVERY-2026-06-12.md),
> finding Rk-1 / Lane-0). **NOT-IN-CONFIG drift.** The stress surface cannot cover the rate moves the
> course asked for because the axis is simply absent from config. Coverage caveat: no 2026-06-11
> risk partition exists on disk, so this is config-and-code only, not verified on delivered output.

## The gap

The course prescribes a **three-axis** stress: spot, vol, **and rate** —
`AlgoTradingCourse2-Consignes.txt l.117-120`: "moins 50% plus 50% de l'espace [spot] … moins 50%
plus 50% de la vol … moins de 10% de [taux]" (rate ±~10%).

`configs/scenarios.yaml` carries:

- `stress_surface.spot_shock_abs: 0.50` (l.16) ✅ matches ±50% spot
- `stress_surface.vol_shock_abs: 0.50` (l.17) ✅ matches ±50% vol
- `spot_shocks` / `vol_shocks` families grids (l.11-12), roll-down
- **no rate-shock grid at all** ❌

The delivered stress surface therefore omits the rate dimension entirely.

## Fix direction

- Add a typed **rate-shock axis** (~±10%, grid TBD by owner) to `scenarios.yaml` and wire it through
  the stress-surface engine (`infra.risk.stress_surface`) and the basket/scenario BFF.
- Decide interaction with the existing spot×vol surface (3-D vs an additional rate sweep) — owner
  ruling needed on grid shape.
- Once a risk partition lands, verify the delivered scenario output actually carries rate-shocked
  valuations (the audit could not — no risk data on disk for 06-11).

## Done criteria

`scenarios.yaml` has a typed rate-shock axis; the engine consumes it; a risk run produces
rate-shocked results; scenarios config-hash golden regenerated; gate green.
