# Workstream C — Analytics core

- **Branch:** `feat/analytics-core`
- **Owns:** `src/snapshots`, `src/forwards`, `src/iv`, `src/surfaces`, `src/pricing`.
- **Roadmap coverage:** steps 5 (spot/snapshots), 6 (forward and carry), 7 (quote QC), 8 (IV solver), 9 (surface), 10 (pricing).
- **Depends on:** A (contracts, fixtures, config). Uses QuantLib and py_vollib.
- **Blocks:** D (needs the pricing interface), E (the actor drives these functions).

## Objective

Build the bespoke pure-function heart. Every module here is a function from one of
A's typed contracts to another, tested entirely against A's fixtures, with no
broker connection and no dependency on the other workstreams running. Keep it
framework-free: Nautilus is only transport, and that is what makes same-code-path
replay possible later. QuantLib/py_vollib do the pricing and inversion heavy
lifting; the bespoke logic is the glue around them.

**Internal ordering matters.** The pricing engine is the keystone the IV solver
inverts and that Greeks and scenarios call. Build and freeze the pricing interface
first, then the rest of C and Workstream D can build against it.

## What you build

1. **Pricing engine** (step 10, build first). Forward-consistent Black-Scholes/
   Black-76 for European (Eq 8–11) and a lattice American pricer with an optional
   Bjerksund-Stensland fast path (Eq 12). A clean typed API: typed state vector in
   (spot, forward, maturity, vol, carry), typed price+Greeks out. This is the only
   module allowed to turn a state vector into a price. Document unit conventions
   rigorously. Benchmark against reference cases; American must converge to
   European in degenerate cases.

2. **Snapshot builder** (step 5). Raw events in, `MarketStateSnapshot` out. Pure:
   `latest_by_field_before(events, snapshot_ts)`, reference spot via mid (Eq 1)
   with documented labeled fallbacks (last/close/carry-forward — never a hidden
   fallback), spread and reference-type flag, options joined with the most recent
   eligible quote within an age threshold, state flags (open/closed, stale
   underlying, stale option, fallback spot), completeness metrics.

3. **Forward and carry engine** (step 6). Per-maturity parity forward from liquid
   near-the-money call-put pairs (Eq 2), liquidity-weighted aggregation (Eq 4),
   MAD outlier rejection (Eq 24), implied carry/dividend diagnostic (Eq 5), a
   confidence score and reason code. Persist the chosen forward and the full
   diagnostics bundle (candidate strikes, mids, weights, per-strike residuals,
   quality labels). Build a single robust point estimate, not a single-pair guess.

4. **Quote normalization and QC** (step 7). Named checks, not a monolithic if:
   spread %, bid positivity, quote age, open interest, monotonicity, crossed/locked
   detection, impossible-vs-intrinsic prices, robust outlier stats on parity
   residuals or preliminary IVs. Mark each quote usable/caution/reject with a
   reason code. Keep both the full and filtered snapshots so QC is auditable.

5. **IV solver** (step 8). Scalar bracketed root solver first (then the vectorized
   batch wrapper), inverting the pricing engine. European inversion via Black;
   American via the chosen pricer or a documented proxy. Intrinsic-value and
   no-arbitrage bounds; record convergence status, iteration count, final residual,
   brackets, and pricing model. Failed solves return structured diagnostics, never
   a bare NaN. Output `IvPoint` with log-moneyness k = ln(K/F) (Eq 6) and total
   variance w = sigma^2 T (Eq 7).

6. **Surface engine** (step 9). Group IV points by maturity; fit in total-variance
   space; SVI per slice (Eq 20) with a nonparametric smoother fallback for sparse
   slices; interpolate across maturities (Eq 22); basic no-arb diagnostics —
   calendar monotonicity (Eq 21) and gross cross-strike checks. Store calibrated
   `SurfaceParameters` and a reconstructed `SurfaceGrid` both, plus fit error,
   accepted-point counts, bound-hit flags. Never discard the raw solved points
   after the fit.

## Acceptance criteria

- Repeated runs on the same fixtures produce byte-identical outputs.
- Pricing reference cases match expected values; the European and American engines
  agree where they should; sign conventions and limiting cases are unit-tested.
- IV: most liquid quotes converge cleanly; pathological cases are labeled, not
  exploded; small input perturbations give plausible IV changes.
- Forward is stable across small changes in the eligible strike set; outliers
  don't dominate; diagnostics explain any maturity flagged poor quality.
- The fitted surface reproduces accepted points within tolerance; sparse/poor fits
  are flagged; a plotting utility can show raw points vs fitted slices.

## Test surface

Cross-cutting rules — independent oracles, the property tests, the edge-case
floor, the coverage floor on this pure core — live in [TESTING.md](TESTING.md).
Read it first. This is the richest edge-case surface in the system; named cases
below are the minimum, not the ceiling. Every expected value cites an independent
oracle (TESTING.md) — no round-trips against your own code except the legitimate
solver-vs-pricer one.

Pricing engine (build and test first):
- Reference values match Hull / a second engine within tolerance; European and
  American agree in the no-early-exercise limit; put-call parity and the sign/
  bound properties are property tests (TESTING.md).
- Limiting cases: `sigma → 0` and `T → 0` → discounted intrinsic; deep ITM/OTM;
  very high vol; `K → 0`. Black-76 vs Black-Scholes consistent under the documented
  carry.
- A unit test that would catch a vol-in-percent-vs-decimal 100× scaling error and
  a maturity-in-days-vs-years error — the conventions you document, asserted.

Snapshot builder:
- The look-ahead boundary: `latest_by_field_before(events, snapshot_ts)` with an
  event timestamped *exactly* at `snapshot_ts` — decide include-or-exclude, test
  it, and never let a later event leak in (the `check-lookahead-bias` skill
  applies here).
- Each fallback rung (mid → last → close → carry-forward) fires under its own
  condition and is labeled; a hidden fallback is a bug, so assert the label.
- Stale flags at the age threshold boundary (exactly-at, just-over); open/closed,
  stale-underlying, stale-option, fallback-spot flags each set on their fixture.
- Empty events, single event, all-stale, crossed quote feeding the mid.

Forward and carry:
- Parity forward from a hand-constructed call/put pair matches the by-hand value
  (Eq 2).
- MAD outlier rejection (Eq 24): inject one outlier strike, assert it is rejected
  and the forward is unchanged within tolerance.
- Stability as a real test: perturb the eligible strike set slightly, assert the
  forward moves less than a documented bound.
- Degenerate: a single eligible pair; no eligible pairs returns a low-confidence
  result with a reason code, not a crash. Zero-liquidity strike contributes ~0
  under the weighting (Eq 4).

IV solver:
- Recover a known `sigma` via price-then-invert (the solver-vs-pricer oracle).
- Monotonicity: higher price → higher IV.
- Price below intrinsic and price above the `F·DF` max each return a structured
  diagnostic (status, iterations, residual, brackets, model), never a bare NaN.
- A genuine non-convergence case returns those diagnostics too.
- `k = ln(K/F)` (Eq 6) and `w = sigma²·T` (Eq 7) computed correctly; deep-OTM
  near-zero-vega robustness; American inversion via the chosen pricer.
- Small price perturbation → small, plausible IV change (a bound, not prose).

Surface:
- Fit recovers known SVI parameters from SVI-generated points within tolerance
  (Eq 20); the fit reproduces accepted points within the documented tolerance.
- No-arb diagnostics flag a violating input: calendar non-monotonicity (Eq 21)
  and a cross-strike butterfly breach are each fed a violating fixture and
  asserted flagged.
- A sparse slice triggers the nonparametric fallback and is labeled; bound-hit
  flags set when SVI params hit their bounds; the raw solved points are retained
  after the fit (assert they are still queryable).

Seam test (you own it, per TESTING.md): every derived contract C emits
round-trips through A's adapter and carries a complete provenance stamp; freeze
the pricing interface and pin its shape so a change breaks D's suite loudly.

## Invariants you own

Determinism (pure functions, no wall-clock, no randomness) and provenance (stamp
every output via A's helper). Quality is visible, not hidden: every fallback,
rejection, and low-confidence fit is labeled and queryable.

## Gotchas

Keep these functions free of I/O and external service calls — that purity is what
makes replay and unit testing trivial. Debug forward and IV failures by inspecting
quote quality first; most errors originate there, not in the formula. Write the
scalar path readably and test it hard before adding the vectorized layer.
