# pricing — the option pricing engine

TL;DR: the one module that turns a typed state vector into a price and Greeks.
Everything else in the platform that needs a model price calls through here.

```python
from pricing import from_forward, price, price_european, pricing_result

state = from_forward(
    forward=100.0, strike=100.0, maturity_years=0.25,
    volatility=0.20, discount_factor=0.99, option_right="C",
)
greeks = price(state)          # -> PriceGreeks(price, delta, gamma, vega, theta, rho)
```

This is the frozen interface the IV solver and the risk engine (Workstream D)
build against. Its shape is pinned by a test (`tests/test_pricing.py`), so changing
a field name or signature breaks D's suite loudly rather than silently.

## What it does

European options are priced in closed form with the forward-consistent Black-76
formula (`DF * (F*N(d1) - K*N(d2))`); the Greeks are the generalized
Black-Scholes-Merton partials with cost of carry. American options are priced on a
QuantLib Leisen-Reimer binomial lattice, with the Bjerksund-Stensland closed-form
approximation available as an optional fast price path. `vollib` and QuantLib were
used as independent cross-checks during development; the European path itself is
self-contained closed form so the unit conventions below are exactly the ones
documented, not a library's.

## Unit conventions (these are the bugs people hit)

These are asserted by the convention tests, not just written here:

- **Volatility** is an annualized decimal: `0.20` is 20%, not `20.0`. A
  percent-scaled input prices a wildly different option.
- **Maturity** is a year fraction: `0.25` is three months, not 0.25 days and not
  91. The American lattice discretizes this horizon to whole Actual/365 days; for a
  maturity derived from an actual expiry date under Actual/365 that is exact, and
  for an arbitrary float it is a sub-day approximation.
- **Discount factor** is `exp(-r * maturity_years)`, in `(0, 1]`. The engine
  discounts with it directly and derives `r` from it only for the American lattice
  and for rho.
- **Carry** `b` is the cost of carry: `b = r` for a non-dividend equity, `b = 0`
  for a future (Black-76), `b = r - q` for a continuous dividend yield `q`.
- **Forward** is authoritative for the European price and must satisfy
  `forward == spot * exp(carry * maturity_years)`. `PricingState` enforces this at
  construction, so the forward-form price and the spot-form Greeks cannot disagree.
  Build states with `from_forward` (forward-space callers: the IV solver, the
  forward engine) or `from_spot` (you have a spot and a carry).

## Greek conventions

- `delta` — spot delta, `dPrice/dspot`. Call delta is in `[0, 1]`, put in `[-1, 0]`
  for a non-dividend underlying.
- `gamma` — `d2Price/dspot2` (>= 0).
- `vega` — per 1.00 of vol (>= 0); divide by 100 for a one-vol-point move.
- `theta` — per year of calendar time, `dPrice/dt` (time decay, so usually < 0 for
  a long option); divide by 365 for a one-day figure.
- `rho` — per 1.00 of rate, holding the forward fixed, so `rho = -T * price`.

`pricing_result(...)` projects these into A's `PricingResult` contract and adds the
monetized Greeks, per unit of underlying (the risk engine multiplies by the
contract multiplier and quantity): `cash_delta = delta * spot`,
`cash_gamma = gamma * spot**2`, `cash_vega = vega * 0.01`. The provenance stamp is
built by the caller with an injected `calc_ts` and passed in, so the engine itself
reads no wall clock and is a pure function of its inputs.

## Limiting cases

`sigma -> 0` or `maturity -> 0` returns the discounted intrinsic with zero gamma
and vega — the engine is total over its whole domain rather than dividing by zero.
Deep in/out of the money and very high vol are unit-tested against their analytic
asymptotes.

## Worked example

The at-the-money reference point used across the tests: `F = K = 100`, `T = 0.25`,
`vol = 0.20`, `DF = 0.99`. Because `F == K`, the call and the put are equal, and the
forward-form Black-76 price is `3.947884` (to six decimals). `test_pricing.py`
cross-checks this three ways — the fixture's own closed-form Black-76, `vollib`, and
QuantLib's `blackFormula` all agree to six decimals — and it is the anchor for the
unit-convention guards: a percent-scaled vol (`0.002`) prices near zero, a
hundred-times-scaled vol (`20.0`) saturates near `DF*F = 99.0`, and only the
correct decimal `0.20` gives `~3.95`. A second textbook anchor (Hull example 15.6:
`S=42, K=40, r=0.10, sigma=0.20, T=0.5`, non-dividend so `b=r`) prices the call at
`4.76` and the put at `0.81`.

## Determinism and the C-layer boundary

These are framework-free pure functions. No wall clock, no RNG, no I/O: the same
`PricingState` always yields the same `PriceGreeks`, byte for byte, which is what
lets a replay reproduce a stored price exactly. The American lattice anchors to a
fixed valuation date (`2025-01-15`) and reconstructs the horizon from
`maturity_years` under Actual/365, so it too is clock-free. Nautilus and the actor
(Workstream E) only feed this module data and carry its outputs to storage; they
never reach into the math. `calc_ts` is injected at the `pricing_result` projection
boundary, never read inside the pricer.
