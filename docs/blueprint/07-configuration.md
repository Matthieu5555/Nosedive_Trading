> Source: blueprint PDF, pages 33–34. Faithful transcription — see ../blueprint/README.md for governance status.

# Part VII — Configuration design and example artifacts

## Configuration philosophy

Configurations are economic inputs. They should never live as scattered constants in notebooks or inside implementation files. Every threshold, bump size, scenario grid, strike-selection rule, and cadence should be represented in a versioned configuration artifact. The configuration package should support inheritance so that a base institutional configuration can be specialized by environment or product family without duplicating the entire tree.

### Suggested configuration files

- environment.yaml — storage paths, service endpoints, log levels, scheduler settings.
- broker.yaml — client IDs, reconnect policy, session windows, market-data cadence.
- universe.yaml — monitored underlyings, exchanges, product families, maturity windows.
- qc.yaml — quote filters, stale limits, solver thresholds, fit tolerances.
- scenarios.yaml — named stress scenarios, shifts, combinations, and report subsets.
- pricing.yaml — solver bounds, finite-difference bumps, pricer choices by product family.

### Illustrative configuration snippet

```yaml
# qc.yaml
quote_filters:
  max_spread_pct: 0.25
  max_quote_age_seconds: 60
  min_open_interest: 10
  require_positive_bid: true

forward_engine:
  strike_band_mode: nearest_liquid
  max_candidate_count: 12
  outlier_method: mad
  max_robust_zscore: 3.5

iv_solver:
  lower_vol: 0.0001
  upper_vol: 5.0000
  price_tolerance: 1.0e-6
  max_iterations: 100

surface:
  model: svi
  fallback_model: spline
  min_points_per_slice: 5
  max_rmse: 0.02
```
