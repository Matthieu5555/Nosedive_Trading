# T-pricing-config-completeness — pricing.yaml missing surface model/fallback + equity forward_engine (ADR-0028 gap)

> **From the 2026-06-12 intent-vs-delivery audit** ([report](AUDIT-INTENT-VS-DELIVERY-2026-06-12.md),
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
2. **Equity forward engine.** Blueprint 07 prescribes `max_candidate_count: 12`,
   `outlier_method: mad`, `max_robust_zscore: 3.5` for the forward engine. Only the **Deribit/Saxo**
   configs carry this block (`forwards_deribit.yaml:16-19` — verified MATCH); the **equity**
   forward engine in `pricing.yaml` has no equivalent, so its candidate cap + outlier policy are
   code defaults.

## Fix direction

- Add `model` / `fallback_model` / `min_points_per_slice` to `pricing.yaml surface:` and read them
  where the literals currently live.
- Add an equity `forward_engine:` block (`max_candidate_count` / `outlier_method` /
  `max_robust_zscore`) to `pricing.yaml`, mirroring the Deribit/Saxo shape, and wire the equity
  engine to read it.
- Regenerate the pricing config-hash golden by design (ADR 0028, C7 pattern).

## Done criteria

The surface model/fallback policy and the equity forward-engine candidate/outlier policy are typed
config values read by the code (no `.py` literals at these sites); pricing config-hash golden
regenerated; gate green.
