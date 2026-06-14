# 0043 — A booked fill is a concrete contract, resolved at booking time

- **Status:** accepted by workspace-owner direction, 2026-06-14. Settles the fork raised by
  `tasks/execution-fill-concretization.md` (found in the 2026-06-14 execution-lane coverage audit).
- **Date:** 2026-06-14.
- **Implements:** the owner ruling — "now, when you book it; I don't think it should be any other
  way." Stays inside [[0011-blueprint-as-plan-of-record]] (the blueprint keys a real *position* by a
  concrete `contract_key` — this honours that, it does not change the option-analytics math) and
  [[0042-index-options-only-scope-ibkr-sole-broker]] (index-only, IBKR sole broker, SX5E live).
- **Relates to:** [[0028-configuration-and-reproducibility-standard]] (the resolution is as-of /
  effective-dated — no look-ahead). Drives `tasks/execution-fill-concretization.md` →
  `tasks/execution-booking-commit.md` → `tasks/execution-fills-position-store.md`.

## Context

The analytics, risk, basket, and order-ticket planes all speak **grid-cell** language: a leg names
`(underlying, tenor_label, delta_band)` — a tenor and a delta band, no strike or expiry — because
`ProjectedOptionAnalytics` is addressed by that grid coordinate. The booking/fills/position plane is
**concrete**: `contracts.Position.contract_key` is `(underlying, strike, expiry, right)` and a fill
carries a price. Nothing in the system translated between the two. WS 3A (the order ticket, landed)
deliberately deferred that translation to "3B" with a `# 3B:` marker; the password-gated booking
commit (§7 #1, the week's top item) assumed it was already done. The translation step was orphaned,
and it blocks the week's #1 deliverable. The fork was: concretize **at booking** (book a real
contract) versus keep fills in grid-cell space for now and concretize only when sending to the
broker.

## Decision

**A booked fill is a concrete contract, and the grid cell is resolved at booking time.** When the
operator books a previewed ticket, the system resolves each grid-cell leg
`(underlying, tenor_label, delta_band)` into a concrete `(strike, expiry, right)` off the captured
chain **as-of the booking date**, marks it at a paper fill price derived from that as-of chain, and
writes a concrete fill keyed by `contract_key`. The book therefore holds real contracts, not loose
descriptions. Risk and attribution gain a concrete-contract valuation path; the grid-cell view stays
the *pre-trade* planning language, not the *booked* one.

Rejected: deferring concretization to broker-send time. It would leave the paper book and the
eventual live book keyed differently (grid cell vs. contract), forcing a rework before live and
breaking the blueprint's per-contract `Position` keying for the interim.

## Consequences

- `execution-fill-concretization` builds a **pure, as-of** resolver `(grid-cell leg, as_of, chain) →
  concrete contract + paper mark` — deterministic, look-ahead-guarded (an old-date replay resolves
  that date's chain, never today's), labelled failure when no contract matches.
- The **paper fill-price rule** is now pinned (built in `packages/execution/concretization.py`):
  the fill books at the **mid of the as-of `MarketStateSnapshot`** (`(bid + ask) / 2`) for the
  resolved contract when a finite two-sided positive quote exists, else the WS-1F analytics row's
  model `price`. The rule that set the mark is recorded on `ConcreteFill.mark_source`
  (`snapshot_mid` / `analytics_model_price`) — deterministic, as-of-clean, never a wall-clock read.
- The grid cell binds to the listed contract at the **soonest listed expiry on/after the booking
  date** (the front contract a desk would book); an already-expired-only listing is a labelled
  failure, never a backward-dated contract.
- `execution-booking-commit` consumes the resolved+marked leg to synthesize the concrete fill;
  `execution-fills-position-store` keys positions by `contract_key`.
- Risk/attribution need a concrete-contract valuation path alongside the grid-cell one — the one
  genuinely new piece of build this ruling implies.
- Paper and (future) live books are keyed identically, so 3B's broker-send binds the *same* contract
  the paper book already holds — no re-keying at the live boundary.
