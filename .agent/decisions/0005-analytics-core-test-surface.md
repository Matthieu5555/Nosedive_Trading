# 0005 — Analytics-core test surface: calendar no-arb property and Black-76/Black-Scholes carry consistency

- **Status:** accepted
- **Date:** 2026-06-02

## Context

A completeness audit of Workstream C against `tasks/TESTING.md` and the spec test
surface (`tasks/03-analytics-core.md`) found two named obligations present as
*outcomes* but not as the *tests* the contract requires. Both are test-surface
gaps, not implementation defects — the code under them was already correct and the
branch-coverage floor on the pure core was ~99%.

1. **Calendar no-arbitrage as a *property* (Eq 21), not just an example.**
   `TESTING.md` lists "total variance non-decreasing in maturity" among the
   property-based invariants C owns. Only an example test of the `calendar_violations`
   detector on one hand-built fixture existed
   (`test_calendar_monotonicity_flags_a_decreasing_slice`); nothing asserted the
   invariant over a range of inputs.

2. **"Black-76 vs Black-Scholes consistent under the documented carry."** The carry
   convention (`b = r` non-dividend, `b = 0` future, `b = r - q` dividend yield) was
   documented in `pricing/state.py` and structurally enforced by the single
   forward-keyed price path, but no test priced one option both ways and asserted
   agreement.

The *way* each gap was closed involves a non-obvious oracle choice the next agent
would otherwise re-derive — or, worse, mistake for a test of the code against
itself — so it is recorded here rather than left to be reverse-engineered from the
test bodies.

## Decision

1. **The calendar invariant is tested via a flat forward-variance construction as
   the independent oracle** (`test_total_variance_is_non_decreasing_in_maturity`,
   `tests/test_surfaces.py`). A flat forward-variance term structure has total
   variance `w(k, T) = base(k) * T` with `base(k) >= 0` (guaranteed by `a >= 0`,
   `b >= 0`, `sigma > 0`), so `w` is non-decreasing in maturity *by construction* —
   an oracle independent of the surface code. That linear-in-`T` scaling is itself
   SVI with `(a, b) -> (a*T, b*T)`, so each slice is a genuine SVI smile, not a
   contrived input. The property asserts both halves of what C produces, over 200
   Hypothesis examples: the `calendar_violations` detector never false-positives on
   the arb-free surface, and `interpolate_total_variance` stays monotone in maturity
   across a dense sweep *between* the knots (so a bad interpolation weight or bracket
   would surface). It lives in `test_surfaces.py`, next to the existing calendar
   example, not in `test_pricing_properties.py`, because it is a surface invariant.

2. **Black-76/Black-Scholes consistency is a forward-price-equivalence test,
   parameterized over the carry**
   (`test_black76_and_black_scholes_agree_under_the_documented_carry`,
   `tests/test_pricing.py`). A European price is a function of
   `(forward, strike, T, sigma, DF)` alone, so the same option priced as
   Black-Scholes from a spot with carry `b` and as Black-76 from the forward
   `F = spot * exp(b * T)` with carry 0 must agree. The test covers `b = r`
   (non-dividend) and `b = r - q` (dividend yield), asserts the two constructors
   agree, and anchors both to the fixture's independent closed-form Black-76 — so it
   is not a self-referential round-trip. Discounting is always at `r`, never the carry.

Each test names its oracle in a comment at the point of use, and the oracle that is
new (the flat forward-variance construction) is added to the `test_surfaces.py`
module docstring, per the convention that the source of every expected value is
stated where it is derived.

## Alternatives considered

- **Testing the calendar invariant by fitting real `IvPoint` slices and checking the
  fitted surface.** More end-to-end, but it couples the invariant to fit noise and
  makes the fitter its own oracle. The analytic scaling construction isolates the
  invariant and gives a clean independent oracle; the fit path is already covered by
  `test_fit_recovers_known_svi_parameters` and the C->A seam test. Rejected for the
  property test.
- **Leaving B76/BS consistency implicit in the single forward-keyed code path.** The
  bug class is largely precluded structurally, but `TESTING.md` names the test, and a
  later refactor that reintroduced a spot/carry dependence in the European price would
  pass silently without it. The explicit assertion is cheap insurance.
- **Recording this by editing ADR 0004 in place.** ADRs are append-only; 0004 is a
  complete dated record. The audit-and-close cycle is a new entry, mirroring how 0002
  recorded A's post-review hardening rather than rewriting A's original ADR.

## Consequences

- Workstream C now covers every named obligation in its spec test surface and the
  C-relevant `TESTING.md` rules; the audit's gap list is empty.
- The full backend gate is green — `ruff` and `mypy` clean, **369 tests pass** — and
  the branch-coverage floor on the C pure core is unchanged at **99.2%** (floor 90%).
- The two invariants are now regression-guarded: a future change that breaks calendar
  monotonicity in the interpolation, or that makes the European price depend on spot
  or carry rather than the forward, fails C's own suite immediately.
