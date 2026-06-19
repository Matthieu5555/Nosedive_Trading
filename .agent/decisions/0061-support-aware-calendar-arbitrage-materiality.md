# 0061 — Support-aware calendar-arbitrage materiality: page only inside observed strike support

- **Status:** accepted, 2026-06-19 (owner ruling, Matthieu).
- **Date:** 2026-06-19.
- **Relates to:** [[0060-scope-aware-eod-qc-severity-index-strict-constituents-notice]] (orthogonal,
  additive: 0060 re-scoped the calendar gate *by underlying* role; this one refines what counts as a
  *material* inversion *within* a single underlying, and the two compose — an inversion must be
  gross AND within observed support to page, then scope still applies),
  [[0052-qc-coverage-floors-to-blueprint-interpolate-and-fallback]] (same lineage: don't let a gate
  fire on the extrapolated edge of the surface where the marks are a fallback, not market data),
  [[0028-economic-config-hashed]] (the new policy toggle + edge tolerance are economic QC cut-offs,
  so they live in hashed `qc.yaml`, not as `.py` literals).

## Context

The EOD pipeline builds one vol surface per underlying, then `check_calendar_sanity` runs the
calendar-arbitrage diagnostic. The no-arbitrage condition is on TOTAL IMPLIED VARIANCE, not on
implied vol: for a fixed log-moneyness `k`, total variance

    w(k, T) = IV(k, T)^2 * T

must be NON-DECREASING in maturity `T`. Implied vol may legitimately FALL with maturity (an inverted
vol term structure is a normal market state); that is not arbitrage. Only total variance falling
with maturity (`w(k, T_short) > w(k, T_long)` for `T_short < T_long`) is a calendar-spread arbitrage.
A breach gross enough to clear the absolute + relative tolerances (ADR 0052) is classified MATERIAL
and escalates to CRITICAL, which PAGES and blocks the date from banking.

On 2026-06-18 SX5E (the one tradeable index, so strict under ADR 0060) PAGED on a material calendar
inversion. Reading the real store (`data/derived/surface_parameters` + `iv_points`, trade_date
2026-06-18), every inverting bucket is in a region the SVI fit EXTRAPOLATED:

- The gross pair at `T ≈ (0.0411 → 0.0603)` years (the short call wing, the prompt's "19d vs 28d"
  neighbourhood) inverts at the `k = +0.10` and `k = +0.20` buckets. The observed log-moneyness
  envelope of those two slices is only `~[-0.107, +0.081]`, so `+0.10`/`+0.20` sit OUTSIDE the
  observed support — the SVI invented those marks with no market quotes behind them.
- The other gross pair at `T ≈ (0.0329 → 0.0411)` inverts at `k = -0.10`/`-0.20`, with an observed
  envelope of only `~[-0.034, -0.002]` (a near-ATM-only slice), so those are extrapolated too.

We do NOT want to page on arbitrage between two FABRICATED/extrapolated marks. But we MUST keep
paging on an inversion that sits INSIDE the observed strike support of both slices — that is a real,
traded-region defect a PM must act on.

## Decision

**A calendar inversion is MATERIAL (→ CRITICAL / page / block) only when it is both gross in
magnitude AND occurs WITHIN the observed strike support of both slices. A gross inversion in the
extrapolation region is downgraded to NOTICE-level (a WARNING; never pages, never blocks banking).**

1. **Observed-support definition.** For an inversion at log-moneyness `k` between short maturity
   `T1` and long maturity `T2`, "within observed support" means `k` lies within the INTERSECTION of
   the two slices' observed log-moneyness envelopes:

       support = [ max(min_k(T1), min_k(T2)), min(max_k(T1), max_k(T2)) ]

   `k` is within support iff `support_min - epsilon <= k <= support_max + epsilon`. If `k` is
   outside that intersection, at least one side is extrapolated, so the inversion is between
   fabricated marks and is downgraded. The intersection (not the union) is the right object: an
   inversion is only a real cross-maturity defect if BOTH legs are quoted at that `k`.

2. **Where the support comes from.** The observed envelope per slice is the min/max
   `log_moneyness` of that slice's raw market IV points (`SliceFit.raw_points`), the same point
   cloud the SVI is fit to. `infra/actor/driver.py` reads it off each fit and threads it into the
   arbitrage builder via a new `CalendarSlice` (maturity, total-variance callable, observed
   `[k_min, k_max]`). `calendar_violations` computes the per-pair intersection once and stamps it
   onto each `CalendarViolation` as `support_min` / `support_max`. The classifier then needs no new
   inputs at the QC seam — the bounds travel with the violation.

3. **Classification, layered on the existing gross test.** `check_calendar_sanity` now sorts each
   violation into three buckets: `noise` (not gross — the ADR-0052 sub-threshold/ultra-short test,
   unchanged), `extrapolated` (gross but `k` outside observed support — the new bucket), and
   `material` (gross AND within support). Only `material` pages CRITICAL; `noise` or `extrapolated`
   is at most a WARNING; an empty set is a clean PASS. ADR-0060 scope (`is_index`) then applies on
   top, unchanged.

4. **Backward-compatible degrade.** A `CalendarViolation` carrying no observed bounds
   (`support_min`/`support_max` = `None`, the dataclass default) is treated as within support —
   support cannot be ruled out, so a gross inversion stays material. Every pre-existing
   unit-test violation (constructed without bounds) therefore keeps its prior classification, and
   the change is a strict additive refinement, not a reinterpretation of old inputs.

5. **Config (hashed, reversible, default-new).** Two fields join `qc.yaml`'s `grid:` block and
   `GridQcConfig`:
   - `calendar_support_aware: true` — the policy toggle. Default ON (the more-correct behaviour).
     Set `false` to restore the pre-0061 all-buckets gate verbatim.
   - `calendar_support_epsilon: 0.000001` — log-moneyness edge tolerance, so a bucket landing
     exactly on the last observed strike counts as inside.
   These are economic cut-offs, so they are hashed in `qc.yaml` per ADR 0028. Adding them rehashes
   the `qc` config bundle (the live hash recomputes from the YAML); no golden test pins the real
   `qc` hash (the determinism/golden suite uses stubbed `cfg-hash-*` config hashes), so the goldens
   are byte-identical and were NOT regenerated.

## Math, stated precisely

- No-arbitrage object: total variance `w(k, T) = IV(k, T)^2 * T`, required non-decreasing in `T` at
  fixed `k`. NOT implied vol — IV may fall with `T` without arbitrage.
- A violation at `k` is `w(k, T_short) - w(k, T_long) > 0`.
- Gross (ADR 0052): the gap clears the absolute tolerance AND the relative-to-`w_long` tolerance,
  and `T_short` is at/above the ultra-short floor.
- Material (this ADR): gross AND `support_min - eps <= k <= support_max + eps`, where
  `[support_min, support_max]` is the intersection of the two slices' observed log-moneyness
  envelopes.

## Consequences

- **SX5E stops paging on this defect, and that is correct.** On 2026-06-18 SX5E's calendar_sanity
  drops from CRITICAL/fail to WARNING/warn: all of its gross inversions are in the extrapolated
  region (`+0.10`/`+0.20` outside `~[-0.107, +0.081]`; `-0.10`/`-0.20` outside `~[-0.034, -0.002]`).
  The desk is no longer paged on arbitrage between marks the fit invented.
- **A genuine within-support inversion still pages.** If SX5E (or any index-scoped surface) inverts
  at a `k` both slices actually quoted, it is still material → CRITICAL → page → block. Verified
  against the real store: of the 14 names that had been paging
  (SX5E, SIE, ADS, ALV, DHL, AIR, SAP, OR, AI, ISP, WKL, SAN, RMS, ENEL), NONE retains a
  within-support CRITICAL — every inversion present on 2026-06-18 is extrapolation-region. The 13
  constituents were already non-paging under ADR 0060 (scope); SX5E is the one the support test
  carries from CRITICAL to WARNING. An otherwise-clean date can now bank healthy.
- **No served/stored marks change.** This is purely a QC severity-classification change.
  `surface_parameters`, `surface_grid`, and `projected_option_analytics` are built in
  `_build_surfaces` / `_build_projected_analytics`, entirely upstream of and independent from
  `check_calendar_sanity`; the new `CalendarSlice` / support bounds are consumed ONLY by the QC
  path. The `QcResult` schema is unchanged (the new `extrapolated_count` and `support_min`/
  `support_max` are additive context keys).

## Honest scope note: the extrapolated buckets are still served downstream

The very buckets this ADR stops paging on (`k = ±0.10`, `±0.20`) are also persisted: the EOD driver
builds `surface_grid` over the SAME `moneyness_buckets` (`-0.2, -0.1, 0, 0.1, 0.2` from
`pricing.yaml`), so the extrapolated marks at `±0.10`/`±0.20` land in the stored `surface_grid` and
are consumed downstream. (Risk on actual positions queries the SVI at each position's own moneyness,
not the grid buckets, but the stored grid itself carries the extrapolated marks.) **This change only
stops the false PAGE; it does NOT make those extrapolated marks trustworthy.** That the surface is
extrapolating a wing with no quotes is a separate, real concern — see "Deferred long-term fix".

## Deferred long-term fix (option 1)

The principled fix is a calendar-coupled SVI / monotone total-variance repair: fit the term structure
so `w(k, T)` is non-decreasing in `T` by construction (no inversions to classify, extrapolated or
not), or clamp/repair the served wing. That changes served marks and must be reviewed visually before
it ships, so it is deferred and will be built behind a DEFAULT-OFF flag and reviewed against the real
surface before any default flip. This ADR (option 2) is the severity-only refinement that stops the
false page now without touching a single served number.

## Out of scope

Per-name support tuning, repairing the extrapolated wing marks, and any change to the
`moneyness_buckets` grid itself are out of scope and left to the deferred option-1 work.
