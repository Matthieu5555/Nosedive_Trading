# forwards — forward & carry engine (step 6)

Recovers, for one underlying and one maturity, the forward price `F` and the
discount factor `DF` from a chain of call/put pairs, plus the implied carry and
dividend. Pure functions: no I/O, no clock, no randomness. This is the input the
IV solver needs — it inverts option prices that were discounted by `DF`, so it
needs both `F` and `DF`, not just the forward.

## TL;DR

```python
from algotrading.infra.forwards import estimate_forward, ForwardPair, forward_curve_point

pairs = (
    ForwardPair(strike=100.0, call_mid=3.9, put_mid=3.9, liquidity=1.0,
                call_key="<call canonical key>", put_key="<put canonical key>"),
    # ... more strikes ...
)
estimate = estimate_forward("AAPL", maturity_years=0.25, pairs=pairs, spot=99.0)
if estimate.is_usable:
    point = forward_curve_point(
        estimate, snapshot_ts=ts, expiry_date=expiry, day_count="ACT/365",
        source_snapshot_ts=ts, calc_ts=ts, config_hash=cfg_hash,
    )  # a stamped ForwardCurvePoint, ready for the store
```

`estimate_forward` is **total**: it never raises on data. Every outcome — a clean
fit, a single-pair fallback, no pairs, or a degenerate fit — comes back as a
labeled `ForwardEstimate` carrying a `reason_code`, a `quality_label`
(`good`/`fair`/`poor`), and a `confidence` in `[0, 1]`. Only `forward_curve_point`
raises (`ForwardError`) — and only if you ask it to emit a forward from an estimate
that does not have a usable one.

## How it works

Put-call parity for a European pair on one strike and expiry is `C - P = DF·(F - K)`
(Eq 2). Read across the chain, `y = C - P` is a straight line in `K` with slope
`-DF` and intercept `DF·F`, so one fit recovers both unknowns at once — the engine
never needs a discount factor handed to it. Three stages:

1. **Robust outlier detection (Eq 24).** A Theil-Sen line (median of pairwise
   slopes) gives residuals that a high-leverage wing strike cannot mask itself in,
   then a MAD z-score flags outliers. The scale is floored at a small fraction of
   the price level so a near-perfect chain's MAD can't collapse to float noise and
   flag clean strikes.
2. **Liquidity-weighted estimation (Eq 4).** A weighted least-squares line through
   the inliers gives `DF = -slope`, `F = intercept / DF`. A strike's `liquidity`
   weight scales its pull; a zero-liquidity strike drops out entirely.
3. **Carry and dividend (Eq 5).** `r = -ln(DF)/T`, `b = ln(F/spot)/T`, `q = r - b`,
   when a positive `spot` is supplied.

A single pair is one equation in two unknowns, so it cannot identify both `F` and
`DF`; pass `fallback_discount_factor` to get a (low-confidence, labeled) forward
from parity anyway, or accept the `single_pair_no_discount_factor` reason.

## The rich estimate vs the persisted contract

`ForwardEstimate` is deliberately richer than A's `ForwardDiagnostics`. The contract
persists the forward and a flat diagnostic summary; the estimate also keeps the
discount factor, the implied carry/dividend, and every strike's weight, residual,
and rejected flag. Thread the estimate from here into the IV solver (which needs
`DF`); project to the contract with `forward_curve_point` for storage.

## Inputs you provide

- `ForwardPair.liquidity` — a non-negative weight proxy (inverse spread, quoted
  size, or open interest). The engine weights by it; near-the-money selection is
  the caller's job. Zero means "ignore this strike".
- `spot` — the reference spot for carry/dividend; omit it and those stay `None`
  (the forward and `DF` still come back from parity alone).
- `fallback_discount_factor` — only consulted for the single-pair case.

## Tunable constants (top of `estimate.py` and `parity.py`)

- `_MAD_REJECTION_Z = 3.5` — robust z-score cutoff for outlier rejection.
- `_RESIDUAL_REL_FLOOR = 1e-4` — outlier-scale floor as a fraction of the price
  level, so the MAD scale can't collapse on a clean chain.
- `_GOOD_REL_RESIDUAL`, `_FAIR_REL_RESIDUAL`, `_FULL_CREDIT_PAIRS`,
  `_REL_RESIDUAL_HALFLIFE`, `_SINGLE_PAIR_CONFIDENCE` — the quality/confidence
  heuristic. These shape every consumer's trust in a maturity; change them
  deliberately.

## Worked example

Single pair, by hand (Eq 2): a call mid `6.0` and put mid `4.0` at strike `100` with
a supplied `DF = 0.95` imply `F = K + (C - P)/DF = 100 + 2.0/0.95 = 102.1052...`.

Full chain: build `y = DF·(F - K)` at strikes `(100, 110, 120)` for a chosen
`DF = 0.90`, `F = 110`; the weighted least-squares line returns exactly
`slope = -0.90`, `DF = 0.90`, `F = 110`. The synthetic surface fixture
(`F = 100`, `DF = 0.99`, `T = 0.25`, spot `= F·DF = 99.0`, five strikes) recovers
`forward ≈ 100`, `discount_factor ≈ 0.99`, `confidence = 1.0`, `quality_label =
"good"`, and the implied carry/dividend: `r = -ln(0.99)/0.25`,
`b = ln(100/99)/0.25`, `q = r - b = 0` (carry equals the rate, so no dividend).
Corrupt one strike's call mid and exactly that strike comes back `rejected`, with
the recovered forward unchanged to `1e-6`.

## Determinism and the C-layer boundary

Framework-free pure functions: no clock, no RNG, no I/O. The fit is order- and
caller-independent, and `calc_ts` is injected only at the `forward_curve_point`
projection. The actor (Workstream E) assembles the `ForwardPair` chain from snapshots
and persists the emitted `ForwardCurvePoint`; it never reaches into the regression or
the outlier rejection. Thread the rich `ForwardEstimate` (which keeps `DF`) directly
into the IV solver in-process.

## Verify

```
uv run ruff check packages/infra/src/algotrading/infra/forwards \
  && uv run mypy packages/infra/src/algotrading/infra/forwards \
  && uv run pytest -q packages/infra/tests/test_forwards.py
```
