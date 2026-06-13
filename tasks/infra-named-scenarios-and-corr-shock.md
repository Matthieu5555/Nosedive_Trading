# infra-named-scenarios-and-corr-shock — named historical stress (2008/COVID) + correlation-shock axis

> **Source:** TARGET §5.4 (the risk manager's screen) + §3 S2 ("this strategy is the reason
> the book needs the stress screen"). The spot×vol full-reprice grid (2B) and the rate axis
> ([[infra-scenario-rate-axis]]) are landed; the **named historical scenarios** and the
> **correlation shock** are the two §5.4 axes that still have no task.

## The gap
`infra.risk.scenarios` / `stress_surface` carry the spot, vol, and (just-landed) rate shock
families. TARGET §5.4 prescribes four axes: spot ±X%, vol ±X pts, rates ±X bp (done), correlation
shocks, **and named historical scenarios (2008, COVID)**. Two are absent:

- **No named-scenario family.** There is no way to express "reprice the book under the 2008 or
  the COVID-March-2020 joint spot/vol/rate move" — a single labelled compound shock, the
  course's 2008 reference behaviour (S2, course p.130) the stress screen exists to show.
- **No correlation-shock axis.** Becomes meaningful only once S1 (dispersion) exists and the
  book carries cross-name correlation exposure (Eq 23, `risk/basket.py`), but the *axis* is a
  §5.4 capability the scenario engine owes.

## Scope (compute only — engine + config; BFF/front deferred, owner-ruled like the rate axis)
- A **named-scenario** type: a labelled, config-defined **compound** shock (joint spot/vol/rate,
  and later correlation) applied as one full-reprice scenario, surfaced alongside the parametric
  grid. Seed catalogue: `2008`, `covid-2020` (magnitudes from the course/blueprint, owner-tunable
  in `scenarios.yaml`, hashed into `config_hashes["scenarios"]`).
- A **correlation-shock** family on the basket-variance path (Eq 23): bump ρ̄ and reprice the
  implied-correlation-sensitive book. Wire it through `scenario_grid` as a parallel family
  (the same architecture-preserving "separate sweep" form the rate axis chose), added only when
  configured so a correlation-less grid hashes byte-identically.
- Construction-hash discipline: fold each new family into the scenario construction hash **only
  when non-empty**, so existing grids stay byte-identical (the rate-axis precedent).

## Depends on / sequence
Reuses the landed 2B full-reprice grid and the [[infra-scenario-rate-axis]] family pattern.
The correlation axis is only *meaningful* once [[ibkr-constituent-option-capture]] +
[[infra-signal-layer]] give a real ρ̄ exposure — spec the axis now, exercise it then. Named
scenarios stand alone (no S1 dependency) and are the nearer-term half.

## Done criteria
`scenarios.yaml` carries a named-scenario catalogue (≥ 2008 + covid) and an optional
correlation-shock family; the engine reprices a book under a named compound shock and under a
ρ̄ bump; empty families hash byte-identically to today; scenarios config-hash golden regenerated
by design; independent-oracle test on one hand-checked compound shock; look-ahead clean; gate green.
BFF/front wiring of the two axes is deferred / owner-ruled (front-adjacent), like the rate axis.
