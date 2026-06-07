# 0037 — Futures capture deferred: ship forward-only for now

- **Status:** accepted, 2026-06-07 (owner has expressed forward-is-primary).
- **Date:** 2026-06-07.
- **Amends:** nothing — this is a **deferral**, so no blueprint amendment is needed (a
  greenlight would have required one to introduce the futures product). Futures remain
  absent from the blueprint by design until this decision is revisited.
- **Relates to:** [[0011-blueprint-as-plan-of-record]], [[0029-contract-field-names-conform-to-blueprint]].
  Closes the **OQ-4 futures fork** in [`open-questions.md`](../open-questions.md) and sets
  the gate for **1D** ([`../../tasks/1D-futures-term-structure.md`](../../tasks/1D-futures-term-structure.md)).

## Context

P0.4 asks whether to capture listed futures now or defer. Futures are **absent from the
blueprint**. The forward path is already built and **primary**: the put-call-parity-derived
forward (`ForwardCurvePoint`) is what Black-76 pricing, IV, forward-delta, and implied
dividend all reference, and backing it out of the chain keeps it self-consistent with the
market's repo/dividend. Listed futures would be, at most, a **secondary** cross-check or
hedge instrument. There is no silent third option — the call must be in writing.

## Decision

**Ship forward-only for now.** No futures product is introduced into the blueprint, the
contracts, or the registry in this increment. The forward path is built, primary, and
sufficient for the analytics the platform delivers (pricing, IV, surface, Greeks, scenarios).

Futures capture is a **later increment behind this same decision**: if a concrete need
appears (a futures-basis cross-check, a listed-futures hedge), it is greenlit by a follow-up
ADR that (a) amends the blueprint to introduce the futures product, and (b) defines the
contract — either by **extending `ForwardCurvePoint`** or by **adding a `FuturesPoint`** in
`contracts/tables.py` + the registry — at which point **1D** is unblocked.

## Consequences

- **1D is gated** on a future greenlight ADR; it does not start under the current decision.
  Its spec records this gate.
- No `FuturesPoint`/futures fields land now; the contract surface stays minimal and the
  forward remains the single self-consistent term-structure source.
- Revisiting is cheap and explicit: a new ADR + a blueprint amendment + a contract, with no
  data migration (no futures data exists yet).
