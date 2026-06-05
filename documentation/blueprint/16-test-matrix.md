> Source: blueprint PDF, pages 46–47. Faithful transcription — see ../blueprint/README.md for governance status.

# Part XVI — Extended test matrix

A production handover is much stronger when the tests are presented as a matrix rather than as a loose list. The matrix below is not exhaustive, but it shows how to think about coverage across module type, data condition, and expected outcome.

| Module | Condition | Test input | Expected result | Priority |
|---|---|---|---|---|
| Spot builder | Normal market | Tight underlying bid/ask | Reference type = mid; spread diagnostics valid | High |
| Spot builder | Wide market | Bid/ask spread beyond threshold | Fallback triggered and labeled | High |
| Forward engine | Liquid maturity | Multiple near-money call/put pairs | Stable weighted forward with high confidence | High |
| Forward engine | Sparse maturity | Two poor-quality pairs only | Low-confidence or rejected maturity | High |
| IV solver | Normal quote | Tradable mid in no-arbitrage bounds | Converged implied vol with diagnostics | High |
| IV solver | Bad quote | Price below intrinsic value | Structured failure, no silent NaN | High |
| Surface engine | Dense slice | Many accepted points | Stable fit with low RMSE | High |
| Surface engine | Sparse slice | Few accepted points | Fallback fit or fail flag per policy | High |
| Pricing engine | Degenerate American case | No early-exercise value expected | American and European prices close | Medium |
| Risk aggregation | Multiple positions | Known synthetic book | Aggregates reconcile to line items | High |
| Scenario engine | Configured grid | Frozen snapshot and positions | Scenario count complete and deterministic | High |
| Replay pipeline | Historical day | Stored raw partitions | Replay equals same-code live logic | High |

### Regression-candidate dataset design

Create a curated library of replay days. The library should include calm days, event-heavy days, days with sparse liquidity, and days containing known operational incidents such as brief disconnects. Each replay day becomes a standard challenge set for new releases. This is one of the highest-leverage investments the team can make because it prevents regressions from hiding behind good behavior on only one benign day.
