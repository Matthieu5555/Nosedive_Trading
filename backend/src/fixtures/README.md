# fixtures

The shared, immutable test substrate: named option chains for the edge cases the
analytics must survive, known-answer generators that act as oracles, and one valid
baseline record per table. Every workstream imports these by name so its tests
bind to one curated home instead of scattering ad-hoc inline literals.

## Why this exists

Tests across four workstreams need the same pathological inputs — a crossed quote,
a stale option, a single-strike maturity — and the same known-good records. If
each test invented its own, the edge cases would drift, two tests would disagree
on what "a stale quote" means, and the analytics code could end up tested against
itself. So the fixtures live here, once, immutable and named. A test references
`get_fixture("crossed_quote")` or `baseline_records()["iv_points"]`, never a
hand-typed literal. This is a test-only module: nothing in the production path
imports it.

A second, subtler reason: the synthetic fixtures are *oracles*. They let the
analytics code be checked against an answer derived independently of the code, not
against its own output — which is the house rule for numerical tests.

## What is here

Import from `fixtures`:

- the chain library — `get_fixture(name)`, `fixture_names()`, `ALL_FIXTURES`, and
  the builders `make_option` / `make_underlying`;
- the chain shapes — `ChainFixture`, `OptionQuoteFixture`;
- the synthetic oracles — `build_synthetic_surface`, `SyntheticSurface`,
  `SyntheticPoint`, and the closed-form `black_call` / `black_put` /
  `parity_forward` / `svi_total_variance`;
- the baseline records — `baseline_records()`.

Two more modules are imported directly (`fixtures.events`, `fixtures.positions`)
by the workstreams that extend the library; they are described below.

## The rogues' gallery — named pathological chains

`fixtures.library` enumerates every pathology the analytics code must survive as a
named `ChainFixture`. A chain is a small immutable bundle: a name, a description,
an as-of instant, the underlying key and spot, and a tuple of `OptionQuoteFixture`
quotes (bid/ask/last and the quote's own timestamp). Quotes are kept at the
raw-ish quote level, not as finished snapshots, so the consuming code still does
its own work — the fixture is the input, not the answer. All chains share a fixed
as-of of `2026-05-29 15:30 UTC`, so staleness and time-to-expiry are reproducible.

| Fixture | What it is |
|---------|-----------|
| `liquid_aapl`, `liquid_msft`, `liquid_spy` | Five strikes, both rights, tight two-sided quotes. |
| `crossed_quote` | A crossed/locked quote: bid (2.50) above ask (2.00). |
| `zero_bid` | A zero-bid quote (bid=0) and a one-sided quote (ask only, bid `None`). |
| `single_strike_maturity` | A maturity with exactly one strike — a degenerate surface slice. |
| `missing_multiplier` | A contract whose multiplier is missing, encoded as `0.0`. |
| `missing_currency` | A contract whose currency is missing, encoded as `""`. |
| `stale_option` | A quote older than the staleness threshold. |
| `negative_or_zero_tte` | Options with negative time-to-expiry (expired) and zero (expiring today). |
| `synthetic_known_answer` | Prices generated from chosen sigma and SVI parameters; forward, vols, and fit are all recoverable. |

The encoding conventions for "missing" are deliberate and documented in
`quotes.py`: a one-sided quote sets `bid` or `ask` to `None`; a zero bid is
`bid == 0.0`; a missing multiplier is `multiplier == 0.0` on the key; a missing
currency is `currency == ""` on the key. `get_fixture(name)` raises `KeyError` for
an unknown name; `fixture_names()` lists them all, sorted.

## The known-answer generators — the oracles

`fixtures.synthetic` is the independent answer key. It picks the true volatility
and true SVI parameters, generates option prices from them, and stores both the
prices and the true answers. The analytics workstream then inverts the prices and
must recover what was put in — so the IV solver, the forward engine, and the
surface fitter are never tested against their own output.

The math is deliberately small and closed-form, with no scipy dependency (the
normal CDF uses `math.erf`):

- Black-76 forward-form call/put, so put-call parity holds exactly:
  `call - put = discount_factor * (forward - strike)`;
- the parity forward: `forward = strike + (call - put) / discount_factor`;
- the raw SVI total-variance slice:
  `w(k) = a + b * (rho * (k - m) + sqrt((k - m)^2 + sigma^2))`.

`build_synthetic_surface(...)` ties them together: for each strike it computes
`k = ln(K / forward)`, the SVI total variance `w`, the per-strike vol
`sigma_k = sqrt(w / T)`, and the Black-76 call and put at that vol. It returns a
`SyntheticSurface` carrying everything needed to check recovery — the true forward
and discount factor (recover via parity), the per-strike vols (recover via the IV
solver), and the SVI parameters (recover via the fit). Degenerate inputs
(non-positive maturity or vol) fall back to discounted intrinsic value, keeping the
generators total rather than raising. The defaults (forward 100, DF 0.99, T 0.25,
SVI `a=0.04, b=0.10, rho=-0.30, m=0.0, sigma=0.20`) are the surface behind the
`synthetic_known_answer` chain.

## The baseline records — one valid row per table

`fixtures.records.baseline_records()` returns a fresh mapping of table name to one
fully-populated, validation-passing record, for all twelve table families. It has
two jobs: the storage round-trip test iterates all of them (write → read → equal),
and the rejection tests take one and break a single field, so each malformed case
differs from a known-good record in exactly one way. A fresh dict is returned each
call, so a test that mutates a copy cannot disturb another. The derived records
carry a real provenance stamp from `make_stamp`, whose source refs are keyed
exactly as the raw-event table is — `(session_id, event_id)` — so lineage resolves
to one row.

## The workstream extensions

Two modules extend the library for the workstreams whose edge cases need a
different grain, and they are imported directly rather than re-exported from the
package:

`fixtures.events` provides field-level `RawMarketEvent` builders for the snapshot
builder (Workstream C). The chain fixtures are quote-level (one bid/ask/last per
instrument); the snapshot builder instead reads individual events each with their
own `canonical_ts`, so the look-ahead boundary, the staleness threshold, and the
labeled price fallbacks each need events timed deliberately. Named scenarios here
include `boundary_bid_events` (three bids before/at/after the snapshot instant, to
prove the just-after one never leaks in), `threshold_straddle_events` (a quote
aged exactly on the staleness threshold), `crossed_then_last_events` (a crossed
quote that must fall back to last), and `single_last_event` / `single_bid_event`.

`fixtures.positions` provides named `Position` and `ContractValuationInput`
fixtures for the risk engine (Workstream D). The market state matches the one the
risk oracles were derived against (spot 100, carry 0, T 0.25, vol 0.20, DF 0.99,
multiplier 100, European — chosen so the at-the-money call price is the familiar
`3.947884` anchor). `risk_positions()` returns the `pf-risk` portfolio (long 10
C100, short 5 P100, long 3 C105); the rogues here are a low-confidence contract
and a non-USD contract for multi-currency aggregation. Note this module imports
from `risk`, so it is only usable once Workstream D is present.

## Failure modes

`get_fixture` raises `KeyError` for an unknown name. The synthetic generators are
total — they do not raise on degenerate inputs, they fall back to intrinsic value.
`baseline_records()` records all pass `contracts.validate`; that is the point of
them. There is nothing retryable here: these are deterministic, in-memory test
data.

## Fastest way to exercise it

```python
from fixtures import get_fixture, build_synthetic_surface, baseline_records
from contracts import validate

chain = get_fixture("crossed_quote")
print(chain.description)

surface = build_synthetic_surface()          # the default oracle
print(surface.points[0].call_price)          # a price the IV solver must invert

for record in baseline_records().values():
    validate(record)                          # every baseline is valid
```

From `backend/`, the fixtures are exercised throughout the suite; the storage
round-trip in `tests/test_storage.py` and the analytics tests are the heaviest
users. Run `uv run pytest -q tests/test_storage.py` to see the baseline records
round-trip.
