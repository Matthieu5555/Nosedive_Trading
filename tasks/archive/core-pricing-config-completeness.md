# T-pricing-config-completeness — pricing.yaml missing surface model/fallback + forward_engine (ADR-0028 gap)

> **From the 2026-06-12 intent-vs-delivery audit** ([report](platform-intent-vs-delivery-audit.md),
> findings An-4 / Lane-0). **Config-completeness drift, not an active bug** — the values exist as
> `.py` literals, so behaviour is correct today but the **economic intent has no typed config home**,
> which is exactly the ADR-0028 ("economic inputs live in versioned config, never as scattered
> constants") violation this audit class is about: a literal can drift silently with no golden to
> catch it.

## The gaps (vs blueprint 07-configuration.md)

1. **Surface model + fallback + min points.** Blueprint prescribes `model: svi`,
   `fallback_model: spline`, `min_points_per_slice: 5`. `pricing.yaml` `surface:` block (l.18-24)
   carries the SVI **bounds** but **no `model` / `fallback_model` / `min_points_per_slice` keys** —
   the model choice and the SVI→spline fallback policy live as Python literals.
2. **Forward engine.** Blueprint 07 prescribes `max_candidate_count: 12`, `outlier_method: mad`,
   `max_robust_zscore: 3.5` for the forward engine. The per-broker `forwards_deribit.yaml` /
   `forwards_saxo.yaml` that used to carry this block were **deleted with Saxo/Deribit**
   (T-index-only-refactor), so **no** typed config now carries the candidate cap + outlier policy —
   the index forward engine (put–call parity, `ForwardCurvePoint`) reads code-default literals. Give
   it a typed home in `pricing.yaml` (the blueprint Part-07 shape is the reference).

## Fix direction

- Add `model` / `fallback_model` / `min_points_per_slice` to `pricing.yaml surface:` and read them
  where the literals currently live.
- Add a `forward_engine:` block (`max_candidate_count` / `outlier_method` / `max_robust_zscore`)
  to `pricing.yaml` and wire the index forward engine to read it (per the blueprint Part-07 shape,
  now that the per-broker `forwards_*.yaml` are gone).
- Regenerate the pricing config-hash golden by design (ADR 0028, C7 pattern).

## Done criteria

The surface model/fallback policy and the equity forward-engine candidate/outlier policy are typed
config values read by the code (no `.py` literals at these sites); pricing config-hash golden
regenerated; gate green.

## Landed — slice 1: `surface.min_points_per_slice` (the SVI-trust routing threshold)
`SurfaceConfig.min_points_per_slice` (`pricing.yaml surface:`, default 5, validated `>= 5`) now
drives the SVI-vs-nonparametric routing in `surfaces/fit.py` — the `MIN_POINTS_FOR_SVI` `.py`
literal is removed from the routing (it stays in `surfaces.svi` only as the hard
five-parameter identifiability floor, a math invariant). Default 5 = byte-identical routing, so no
analytics/surface golden moved (only the `pricing` config-hash, by design). Tests: a raised
threshold routes a well-populated slice to the fallback; the `>= 5` floor is enforced. Gate green.

## Landed — slices 2 & 3: surface model/fallback + forward-engine policy (2026-06-14)
The two deferred slices landed, resolving their wrinkles honestly:

- **`model` / `fallback_model`** — `SurfaceConfig.model` (∈{`svi`}) / `fallback_model`
  (∈{`nonparametric`}) now carry the method choice; `fit_slice` reads the emitted labels from
  config instead of the hardwired `METHOD_SVI` / `METHOD_NONPARAMETRIC` literals. The "spline"
  wrinkle is resolved by **naming the fallback what the code does** — `nonparametric` (linear
  interp in total variance), never the blueprint's aspirational `spline` (config describes the
  code). The vocabulary is forward-compatible: a real spline would grow both the validator set
  and the fitter's dispatch together. No model-dispatch registry was built — over-engineering for
  two methods; the labels-from-config wiring is the deliverable.
- **Forward-engine block** — landed in the **existing `forward:` block** (not a fragmented
  `forward_engine:` section): `ForwardConfig.max_robust_zscore` (3.5), `outlier_method`
  (∈{`mad`,`none`} — `none` genuinely disables rejection, giving the field teeth), and
  `max_candidate_count` (`int|None`). The shared-util wrinkle is resolved by **parameterising**
  `robust.outlier_flags(..., rejection_z=…)` with the `_MAD_REJECTION_Z` default kept — the shared
  util stays decoupled and every non-forward caller is byte-identical; the forward engine passes
  its config value. The new-cap wrinkle is resolved with the **zero-churn idiom**: `max_candidate_count`
  ships `None`=no-cap (byte-identical), and when set keeps the most-liquid N pairs (tie-break strike);
  the blueprint's `12` is an owner-enabled value (it moves the analytics golden when binding), so it
  is not shipped on.

Every default = the shipped behaviour, so fits/forwards are byte-identical; only the `pricing`
config-hash (and the folded whole-config hash) moved BY DESIGN, section isolation intact. The
pinned synthetic oracle in `test_config_core` was regenerated. (Incidentally reconciled the
`strategy_signals` golden row that predated the as_of provenance column — additive `as_of:null`,
stamp_hash unchanged.) Tests cover config-driven labels, the candidate cap, the `none`/z-cut
outlier knobs, and per-knob hash folding. Gate green (2057 passed / 12 skipped).

## Done — all three slices landed; the surface model/fallback policy and the forward-engine
candidate/outlier policy are typed config read by the code (no `.py` literals at these sites);
the pricing config-hash golden regenerated.
