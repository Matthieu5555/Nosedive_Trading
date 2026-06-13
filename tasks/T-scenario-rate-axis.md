# T-scenario-rate-axis — add a rate-shock axis to the scenario grid (blueprint §5 optional extension)

> **STATUS (2026-06-14): engine + config LANDED** (additive forward-fixed rate-shock family + the
> Rho attribution term that fires under it). Open = optional 3-D stress_surface + BFF/front wiring.
>
> **Framing corrected (coherence audit 2026-06-14):** an earlier version claimed *"the course
> prescribed a rate axis"* — that over-reads a **garbled transcript line**: `Consignes.txt l.119`
> reads *"moins de 10% de l'éducation"* (and the architecture transcript *"…de l'économie"*), **not
> "taux"**. The rate axis is **not** a course prescription; it is (a) a **reasonable inference** (the
> standard third stress axis is spot/vol/rate) and (b) a **blueprint-endorsed optional extension** —
> `05-math-notes.md §5`: *"the grid may later add … rate shocks … can be layered in without changing
> the architecture."* So the work is justified — just not as "the course asked for it."

## The gap (original framing — kept for history; see STATUS above)

The implementation read the stress grid as a **three-axis** stress (spot, vol, **rate**),
inferring rate ±~10% from `AlgoTradingCourse2-Consignes.txt l.117-120` — a line whose third axis is
**inaudible/garbled** (see the corrected framing above). The blueprint §5 endorsement is the firm
anchor; the transcript "prescription" is not.

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

## Landed — engine + config (compute only; BFF/front deferred)
`ScenarioConfig.rate_shocks` (additive absolute rate moves, default `()` → backward-compatible,
no rate family when empty). `scenarios.yaml`: `rate_shocks: [-0.0025, 0, 0.0025]` (±25 bp,
owner-tunable; version bumped 2026.06.13). Engine (`risk/scenarios.py`): `Scenario.rate_shock`,
a `rate` family in `scenario_grid` (order spot→vol→rate→combined→time, added only when
configured), `shock_valuation` shifts the rate **additively, forward-fixed** (only the discount
factor responds — matches the pricer's forward-fixed rho), and `taylor_terms` now passes
`d_rate = scenario.rate_shock` so the **Rho attribution term fires under a rate scenario** (the
loop §7.2 left open). Construction hash folds `rate_shocks` **only when non-empty**, so every
rate-less grid hashes byte-identically; the config-hash golden moved by design (rate axis added).

**Grid-shape ruling (chosen, blueprint-grounded):** the rate axis is a **separate parallel
family** (a rate *sweep*), not a 3-D spot×vol×rate cross-product — the architecture-preserving
form `documentation/blueprint/05-math-notes.md §5` endorses ("layered in without changing the
architecture"). The full 3-D `stress_surface` expansion (and the BFF/front wiring of the rate
axis) stays **deferred / owner-ruled** — front-adjacent, disjoint from this compute lane.
