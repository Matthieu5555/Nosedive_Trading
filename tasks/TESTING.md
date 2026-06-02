# TESTING.md — the shared test-surface contract

Read this before you write a line of code or a line of test. Every workstream
spec points here for the rules that cut across all five; each spec's own
**Test surface** section then names the cases specific to its modules.

This file exists because of one fact about LLM-driven coding: **an agent tests
what is named and skips what is not.** Hand an agent "the forward is stable
across small changes" and you get a green suite that never perturbs an input.
The acceptance criteria in the specs are outcomes, not a test surface. This file
turns them into named, checkable obligations. If a case below is not exercised by
a test, the work is not done — the same standard AGENTS.md already sets, made
concrete.

## The rule that governs the rest

Name the case or it does not get tested. Outcome prose ("converges cleanly",
"byte-identical", "stable") is not a test — it is a target. For every such phrase
in your spec there must be a test whose name is the case and whose assertion is
the bound. Prefer the lowest level that catches the bug class (`conventions.md`),
and write the assertion, watch it fail for the right reason, then implement.

## Independent oracles — never test code against itself

The fastest way to a worthless suite is `assert solve(price(x)) == x` checked
against your own round-trip, or testing a QuantLib wrapper against QuantLib. Every
numeric module must state, in a comment on the test, where the expected value came
from, and it must be a source *independent of the code under test*:

- **Pricing / Greeks** — Hull textbook reference values, or `py_vollib`
  cross-checked against `QuantLib` (two independent engines agreeing is the
  oracle; one engine checked against itself is not).
- **IV solver** — synthetic: pick `sigma`, price it with the *pricing engine*,
  invert with the *solver*, recover `sigma`. The pricing engine is the
  independent oracle for the solver, which is the one place a round-trip is
  legitimate — but only because the two sides are different code.
- **Forward** — a hand-constructed call/put pair whose parity forward you compute
  by hand in the test comment (Eq 2).
- **Surface** — generate points from *known* SVI parameters (Eq 20), fit, recover
  the parameters within tolerance. The generator is the oracle.
- **Risk aggregation** — sum line-level by hand for a 2–3 position fixture; the
  aggregate must equal the hand sum.

If you cannot name an independent oracle for a number, you do not yet understand
the computation well enough to test it. Stop and find one.

## Determinism is a mechanism, not a hope

"Byte-identical" and "same-code-path replay" appear across four specs with no
machinery behind them. Here is the machinery, and it is mandatory:

- **Golden files.** Determinism claims are backed by a committed golden output and
  a single documented regeneration command. The test recomputes and compares to
  the golden artifact; regenerating is a deliberate, reviewable act, never
  automatic.
- **Cross-process hash stability.** `config_hash` and any provenance hash must be
  identical across two *separate* Python processes, not just within one. Test it
  by computing the hash in a subprocess and comparing. This catches the classic
  bug — hashing a `dict`/`set` under hash randomization — that passes in-process
  and silently drifts between runs. Do not rely on `PYTHONHASHSEED` being set;
  the hash must be stable without it.
- **Reordering invariance.** Where input order must not change the output (event
  sets feeding a snapshot, source records feeding a stamp), shuffle the input in
  the test and assert the output is unchanged. Where order *does* matter, assert
  the defined order is enforced.

## Seam tests — the contracts are the only thing crossing boundaries

The architecture's whole bet is that A's typed contracts are the only objects
that cross a workstream line. That bet is only real if it is tested at each seam,
by the *consumer*, before E's integration phase — otherwise drift bakes in for
weeks and surfaces as an integration mess. Each consuming workstream owns a
contract test proving its objects round-trip through A's adapter and validate
against A's schema:

- B → A: `InstrumentMaster` and `RawMarketEvent` write and read back equal.
- C → A: every derived contract C emits (`MarketStateSnapshot`,
  `ForwardCurvePoint`, `IvPoint`, `SurfaceParameters`, `SurfaceGrid`,
  `PricingResult`) round-trips and carries a complete provenance stamp.
- D → A: `Position`, `RiskAggregate`, `ScenarioResult` round-trip and stamp.
- D → C: D builds against C's frozen pricing interface using A's fixtures; a test
  pins the interface shape so a C-side change breaks D's suite loudly, not E's.
- E → all: a cross-cutting test that every C/D output landing in storage carries a
  non-empty, well-formed provenance stamp (this is E's invariant to verify).

A contract test that only checks the happy shape is half a test. Include at least
one malformed instance per contract and assert A's write-ahead validation rejects
it with an explicit error, not a silent coercion.

## Property-based tests for the invariants

This domain is the textbook case for property-based testing (Hypothesis):
relations that must hold over a *range* of inputs, not three hand-picked points.
Each invariant below is owned by the workstream that produces it and must have a
property test, not just example tests:

- Put-call parity `C - P = DF·(F - K)` over random `(F, K, T, sigma)` — C.
- American ≥ European, and American → European in the no-early-exercise limit — C.
- `gamma ≥ 0`, `vega ≥ 0`, call `delta ∈ [0,1]`, put `delta ∈ [-1,0]` — C.
- Price strictly increases in `sigma` (so IV is well-defined and monotone) — C.
- Total variance non-decreasing in maturity (calendar no-arb, Eq 21) — C.
- Aggregate risk invariant under input-position reordering; sum of lines equals
  aggregate — D.
- Same inputs → identical hash/stamp under reordering of source records — A.

## The edge-case checklist every module clears

Beyond its named domain cases, every module is tested against the boring inputs
that break code: **empty, single element, duplicate, the value exactly on a
threshold boundary, NaN/inf, and degenerate shape** (zero strikes, zero
positions, one-point slice). These are not optional and not domain-specific —
they are the floor. A module that has not been fed an empty input and a
boundary-exact input has not been tested.

Negative paths are first-class. The specs are full of "labeled, not exploded",
"raises with diagnostics, never silently skipped", "structured diagnostic, never
a bare NaN". Each of those is a required test: feed the bad input, assert the
*labeled failure*, not a crash and not a silent pass.

## What "everything tested" means operationally

So the bar is falsifiable:

- **Coverage floor on the pure-function core.** C (`src/iv`, `src/forwards`,
  `src/surfaces`, `src/pricing`, `src/snapshots`) and D (`src/risk`) carry branch
  coverage at or above a committed threshold (start at 90%; raise, never lower).
  The transport and orchestration tiers (B, E) are held to behavior tests, not a
  coverage number, because their bugs live in wiring and timing, not branches.
- **No test without a real assertion.** A test that runs the code and asserts
  nothing meaningful is the most common LLM test smell and counts as absent.
  Consider mutation testing on the math core to catch exactly this — a passing
  suite that survives a flipped sign is lying.
- **Silent truncation is a bug.** If a test bounds its own coverage (samples N
  cases, skips a slow path), it says so out loud; a quiet cap reads as "covered"
  when it isn't.

## The fixture library — shared ground, enumerated

A seeds it (spec 01, item 6); C and D extend it; everyone imports it by name. For
LLM coding the pathologies must be *named fixtures that exist*, because the
edge-case tests above bind to them. A's Test surface enumerates the exact minimum
set. The rule for everyone else: an edge-case test references a named fixture from
this library, never an ad-hoc literal built inline, so the pathological inputs
have one curated home and a new agent can see the whole rogues' gallery in one
place.
