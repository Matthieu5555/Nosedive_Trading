# T-pricing-config-completeness — pricing.yaml missing surface model/fallback + forward_engine (ADR-0028 gap)

> **From the 2026-06-12 intent-vs-delivery audit** ([report](T-intent-vs-delivery-audit.md),
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

**Deferred (with their wrinkles, so the next pass has the analysis):**
- **`model` / `fallback_model`** — not a clean literal move: the blueprint says `fallback_model:
  spline`, but the code's fallback is **linear-interpolation nonparametric** (`METHOD_NONPARAMETRIC`),
  not a spline. Adding the keys needs a model-dispatch layer + an owner/blueprint reconciliation of
  the "spline" name vs the shipped linear interp — do not encode `spline` if the code does linear.
- **Forward-engine block** (`max_candidate_count` / `outlier_method` / `max_robust_zscore`) — the
  z-score literal `_MAD_REJECTION_Z = 3.5` lives in the **shared** `utils/robust.py` (used beyond
  forwards → a config home there would couple the shared util to forward config — a layering call),
  and `max_candidate_count: 12` is a **new cap behaviour**, not an existing literal. Needs its own
  small design (where the rate/outlier config lives without cross-layer coupling).
