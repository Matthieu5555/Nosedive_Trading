# 0062 — Calendar variance-floor repair: a monotone total-variance floor on the served grid

- **Status:** accepted, 2026-06-19 (owner direction, Matthieu: build the deferred option-1 fix
  behind a default-off flag).
- **Date:** 2026-06-19.
- **Relates to:** [[0061-support-aware-calendar-arbitrage-materiality]] (this is the served-surface
  counterpart of, and the first step of the "Deferred long-term fix (option 1)" recorded in, ADR
  0061: 0061 stops the false PAGE on extrapolated-wing inversions; this repairs those same marks so
  the stored grid is itself calendar-arbitrage-free), [[0028-economic-config-hashed]] (the new
  toggle + tolerance are pricing-surface cut-offs, so they live in hashed `pricing.yaml`, not as
  `.py` literals), [[0052-qc-coverage-floors-to-blueprint-interpolate-and-fallback]] (same lineage:
  the extrapolated wing is a fallback, not market data).

## Context

Calendar no-arbitrage is a condition on TOTAL IMPLIED VARIANCE, `w(k, T) = sigma(k, T)^2 * T`,
which must be non-decreasing in maturity `T` at fixed log-moneyness `k` (falling IV with maturity
is a normal downward term structure and is fine; only falling total variance is arbitrage). Each
expiry's smile is fit independently (`surfaces/fit.py`). In the EXTRAPOLATED wings — outside the
strikes actually quoted — two independent smiles can cross, giving `w(k, T_long) < w(k, T_short)`:
a calendar arbitrage between two numbers the model invented with no market behind them.

ADR 0061 addressed the QC SEVERITY of this: an inversion outside observed strike support is
downgraded from a CRITICAL page to a non-blocking notice, so SX5E stopped paging on fabricated-mark
crossings. But 0061 explicitly left the served marks alone — its "Honest scope note" records that
the `k = ±0.10`/`±0.20` extrapolated buckets are still persisted into `surface_grid` and consumed
downstream, and its "Deferred long-term fix (option 1)" names the principled repair (a monotone
total-variance floor / calendar-coupled SVI) as the next step, to ship behind a default-off flag and
be reviewed against the real surface before any default flip.

This ADR is that first step: repair the stored grid so it is calendar-arbitrage-free in the corners,
without rewriting any traded mark and without silencing a genuine in-data inversion.

## Decision

**A monotone total-variance floor over the served moneyness grid, gated default-off.**
(`surfaces/repair.py`, `monotone_variance_floor` / `repair_overrides_from_slices`.)

1. **The floor.** Within each underlying, walk expiries short -> long. At every grid point `k`:
   - **inside the slice's observed strike support** (`[k_min, k_max]` of its raw IV points, the same
     envelope ADR 0061 uses, with a `calendar_repair_support_epsilon` edge tolerance): keep the raw
     fit untouched. A real, in-data calendar inversion is left exactly as fit, so the QC still pages
     on it — it is a true signal, not a model artefact.
   - **outside the support** (the model is extrapolating): clamp the value up to the prior repaired
     curve so `w` cannot fall below the shorter expiry. The floor CHAINS — a long wing is pinned to
     the nearest shorter wing, not to two-expiries-ago. The shortest expiry of each underlying is
     never floored.

2. **One source of truth, two consumers.** The repair returns a discrete `{(underlying, T): {k: w}}`
   lookup on the projection grid. It is threaded as an optional `total_variance_by_bucket` override
   into `surface_grid_cells` / `project_surface_fit` (so the served/stored `surface_grid` carries
   the repaired marks) AND into the calendar QC, so the stored surface and its arbitrage check read
   the SAME numbers and cannot disagree. With the flag off, `total_variance_by_bucket` is `None`/an
   empty map and the served mark is the raw fit exactly as before — a bucket missing from the map
   also falls back to the raw fit, so the override only ever lifts the extrapolated wings it was
   built to repair.

3. **Config (hashed, reversible, default-off).** Two fields join `pricing.yaml`'s `surface:` block
   and `SurfaceConfig`:
   - `calendar_variance_repair: false` — the policy toggle. Default OFF. Flipping it ON changes
     served IV values, so per ADR 0028 it is a hashed config behaviour, never a silent default; you
     flip it on, eyeball before/after, then decide.
   - `calendar_repair_support_epsilon: 1.0e-6` — log-moneyness edge tolerance for the "inside
     support" test; only read when the repair is ON.

## Honest scope limit

This repairs the DISCRETE stored grid (the `±10%`/`±20%` buckets) — what is persisted into
`surface_grid` and displayed. It does NOT rewrite the continuous SVI parameters that live risk
evaluates at arbitrary strikes; an off-grid query still reads the raw fit and can still cross in the
extrapolated region. The deeper fix that makes the curves non-crossing BY CONSTRUCTION
(calendar-coupled SVI / SSVI) rewrites surface levels across every name and needs a visual review
before it can be trusted, so it remains the deferred follow-up. This ADR makes the stored surface
and its alarm arbitrage-free in the corners; it does not yet bend the underlying math everywhere.

## Consequences

- **The stored grid is calendar-arbitrage-free in the extrapolated corners when the flag is on**,
  and the surface and its calendar QC agree by construction (same numbers). After repair the
  extrapolated crossings are gone; a genuine in-support crossing still trips the alarm.
- **Default-off, so nothing changes until reviewed.** Served marks are byte-identical to pre-0062
  until the owner flips the flag and compares.
- **Off-grid risk is unchanged** — the continuous SVI is untouched; see the scope limit.
- Covered by 12 unit tests (shortest expiry never floored, in-data crossings preserved, corners
  floored, the floor chaining across three expiries, per-name isolation) and 5 wiring tests proving
  the repaired number reaches both the served grid and the calendar QC. The config-hash test is
  updated for the new flag.
