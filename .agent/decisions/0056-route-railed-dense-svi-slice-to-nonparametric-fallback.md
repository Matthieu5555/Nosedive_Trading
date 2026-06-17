# 0056 — Route a railed, dense SVI slice to the nonparametric fallback (opt-in, default OFF)

- **Status:** accepted, 2026-06-17 (tech-lead ruling, Surface & Analytics family, ratified under a
  **full-authorization owner override** that lifts the prior "owner-gated / future column" deferral
  on this lane).
- **Date:** 2026-06-17.
- **Implements:** the blueprint as plan-of-record ([[0011-blueprint-as-plan-of-record]]) —
  `04-implementation-guides` §"Surface engine pseudocode" (`group_by_maturity` →
  `fit_svi_or_fallback`) and §04.H failure-mode table, the row:
  *"Surface fit failure | … pathological quote set | Publish fail flag and retain raw points for
  review | **Improve fallback interpolation path**"* — this ADR builds that "[longer-term]" column.
  Also `12-file-by-file-guide:70-72` ("bound parameters and log bound hits"; "support at least one
  fallback method for sparse slices") and `05-math-notes:36` ("improvements come from better QC
  rather than a more complicated surface model").
- **Relates to:** [[0052-qc-coverage-floors-to-blueprint-interpolate-and-fallback]] (Lane 1: the QC
  recalibration that fixed the benign `a_lower` false positive; this ADR reuses that exact
  benign-vs-genuine discrimination), ADR 0028 (configuration & reproducibility standard — the typed
  `SurfaceConfig.model`/`fallback_model` home and "served IV values are a hashed config behaviour").

## Context

The 2026-06-15/-16 SX5E validation showed the SVI optimizer **railing a parameter to its bound**
(`svi_rho → −0.999`) on some slices. Lane 1 ([[0052]]) established that the dominant such failure —
`svi_a → 0` with a positive minimum total variance — is a **benign parametrization sink**, not a bad
fit, and exempted it in QC. What remains is a smaller, **genuine** class: a slice that is **dense
enough to fit** (≥ `min_points_per_slice` distinct strikes) yet whose SVI rails an
**economically-meaningful** parameter (e.g. `svi_rho` pinned to its bound), or carries a surviving
butterfly arbitrage, or did not converge. For such a slice the railed SVI is an over-fit on a railed
parameter (tiny RMSE, but a degenerate curve), while the **smooth nonparametric interpolation of the
same dense points is a better-behaved served curve**.

The blueprint's surface pseudocode is `group_by_maturity` → `fit_svi_or_fallback`, and §04.H rules
the current behaviour (serve the railed SVI, flag QC `surface_fit_error`, retain raw points)
**correct, not a deviation** — with "Improve fallback interpolation path" explicitly a *longer-term*
column. The existing sparse-slice fallback (`fit_slice`, `len(ks) < min_points_per_slice`) already
covers the **thin** case. The genuinely **railed-dense** case is the open column this ADR addresses.

The earlier instinct — unconditionally gate `fit_slice` to reroute any railed/arb/non-converged SVI
— was prototyped and reverted (`dbc05c6`), correctly: it would have been a silent default flip of
served IV values, and it conflated the benign `a_lower` sink with a genuine rail. This ADR is the
disciplined version of that idea: opt-in, benign-aware, dense-only.

## Decision

**Add an opt-in routing path: a GENUINELY railed SVI slice that is DENSE ENOUGH to fit serves the
smooth nonparametric fallback instead of the railed SVI. Default OFF — byte-identical goldens until
enabled. Flag-not-reject is preserved.**

The routing predicate (`_should_reroute_railed_dense`, `infra/surfaces/fit.py`) reroutes iff **all**:

1. **opt-in** — `SurfaceConfig.reroute_railed_dense_slice` is `True` (default `False`); AND
2. the slice fit SVI (`method == "svi"`, i.e. it was already on the dense path); AND
3. **dense enough** — `n_points >= reroute_point_floor` (= `reroute_min_points`, defaulting to
   `min_points_per_slice`), so the rail is a model misfit and not a thinness artifact; AND
4. **genuinely railed/degenerate** — `genuine_degeneracy_reasons(fit)` is non-empty: a non-benign
   bound hit (the benign `a_lower` sink from Lane 1 is exempted by the *same* `is_benign_a_floor`
   discrimination QC uses), a surviving butterfly arbitrage, or a non-converged fit.

When it reroutes (`_reroute_railed_to_fallback`), only the **served curve** changes: `method`
switches to `fallback_model` (so `total_variance` and the projected grid come from the smooth
interpolation). **Every SVI diagnostic is carried through unchanged** — `svi` params, `bound_hits`,
`arb_free`, `butterfly_violations`, `rmse`, `converged`. The slice therefore **still FAILS**
`surface_fit_error` on its genuine reason: this is **flag-not-reject** (§04.H) — we are choosing a
smoother *flagged* surface over a railed *flagged* surface, never rejecting the slice. No
`SurfaceParameters` SVI row is persisted for a rerouted slice (matching the existing fallback path),
so the railed SVI is not stored as canonical.

`reroute_railed_dense_slice` and `reroute_min_points` are **economic, hashed config**
(`configs/pricing.yaml`, ADR 0028): flipping the flag changes served IV values, so it is a hashed
config behaviour, never a silent default flip — regenerate the surface config-hash golden when moved.

## Consequences

- With the flag ON, a railed-dense slice (the genuine `svi_rho`-pinned case) serves a smooth,
  monotone-in-points nonparametric curve instead of a railed SVI — the downstream nappe / greeks
  term-structure stop inheriting the railed parameter — while QC still honestly flags the slice.
- With the flag OFF (the shipped default), `fit_slice` is **byte-identical** to its pre-ADR
  behaviour; all canonical goldens are unchanged.
- The benign `a_lower` sink ([[0052]]) is **not** newly rerouted (it is not a genuine reason), and a
  **thin** slice is **not** newly rerouted (it never reached the SVI path); both Lane-1 behaviours
  are preserved by construction and asserted in tests.
- Risk: the nonparametric fallback is a linear interp in total variance (honestly named, not a
  spline), so a rerouted slice is only as smooth as its points. Mitigation: the reroute is gated on
  density (≥ the floor), and it is opt-in — an operator turns it on per the data, with the config
  hash recording the choice.

## Out of scope

A richer fallback model (true spline / SSVI) stays a later release — this ADR routes to the
**implemented** nonparametric fallback (`_SURFACE_FALLBACK_MODELS`), not a new one. Advanced
arbitrage repair stays out (§02 "diagnostics now, enforcement later"); the reroute reads the existing
arb-free flag, it does not repair.
