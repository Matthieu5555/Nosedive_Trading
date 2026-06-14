# execution-fill-concretization — resolve an abstract grid-cell ticket into a concrete, priced paper fill

> **Source:** the execution-lane coverage audit (2026-06-14). The booking chain has a real
> discontinuity that **no existing spec owns**, and it sits on the critical path for the week's
> §7 #1 deliverable. 3A ([[execution-order-ticket]], landed) deliberately defers concrete-contract
> binding to "3B" (a literal `# 3B:` marker). [[execution-booking-commit]] (§7 #1) assumes it can
> "synthesize the paper fill(s) from the ticket" — but it is **not** 3B, and the ticket it receives
> is abstract. This task fills the gap between them.

## The gap
Two planes of the system speak different languages:

- **Analytics / risk / basket / ticket — grid-cell space.** A `BasketLeg` and a `TicketLeg` name
  `(underlying, tenor_label, delta_band)`; there is deliberately no strike or expiry. The risk
  engine (`risk/multileg.py`) values legs *by that grid coordinate* via `ProjectedOptionAnalytics`.
- **Booking / fills / position — concrete space.** `contracts.Position.contract_key` is keyed by
  `(underlying, strike, expiry, right)` and a fill must carry a **price**.

You cannot synthesize a concrete, priced fill from an abstract grid-cell ticket. So before
[[execution-booking-commit]] can write a fill the fills store keys and risk/attribution can read,
**someone must resolve the grid cell to a concrete contract and assign a paper mark.** Today nobody
does: 3A punts it to 3B, booking-commit assumes it is done, [[execution-fills-position-store]]
assumes risk can value the result.

## The ruling (settled — [ADR 0043](../.agent/decisions/0043-fills-are-concrete-contracts-resolved-at-booking.md))
**A booked fill is a concrete contract, resolved at booking time** (owner ruling, 2026-06-14).
Resolve `(tenor_label, delta_band)` → a real `(strike, expiry, right)` off the captured chain
**as-of the booking date**, mark it at a paper fill price from that as-of chain, and book a concrete
fill keyed by `contract_key` (the blueprint's per-contract `Position` shape, §6 fill semantics
intact). Risk/attribution gain a **concrete-contract valuation path** alongside the grid-cell one.
The rejected alternative — keep fills in grid-cell space and concretize only at broker-send — would
key the paper book and the live book differently and force a rework before live; see the ADR.

## Scope
- A **pure resolver** `concretize(ticket_leg, as_of, chain) -> ConcreteContract` — no I/O beyond the
  as-of chain read, no wall clock.
- A **paper mark / fill-price source**: the price a simulated fill books at, derived from the
  captured chain/surface as-of the booking date (mid, or a stated rule) — deterministic and
  as-of-clean, never a wall-clock read.
- The **fill record shape** this produces is the seam [[execution-booking-commit]] consumes and
  [[execution-fills-position-store]] ingests — co-design the field names, one source not three.
- Index-only, IBKR sole broker, SX5E sole live index (TARGET §0 / ADR 0042). Paper/read-only — this
  resolves and prices a *paper* fill; it touches no broker and reads no credential.

## Why
- TARGET §6: accounting is **from fills**, and a fill is concrete and priced — the grid-cell ticket
  is not yet a fill. This task is the missing transform.
- TARGET §7 #1 (the week's top item) cannot complete without it: [[execution-booking-commit]] has no
  honest fill to synthesize until the grid cell is resolved and marked.

## Test surface (named)
- **Concretization is deterministic + as-of:** the same `(grid cell, as_of, chain)` resolves to the
  same `(strike, expiry, right)` every time; an as-of replay of an old date resolves the *old*
  chain's contract, never today's (look-ahead guard).
- **Mark is as-of + reproducible:** the paper fill price is derived from the as-of chain by the
  stated rule; independent oracle = hand-computed mark from a fixture chain.
- **Seam round-trip:** the fill record this emits is exactly what [[execution-booking-commit]] expects
  and [[execution-fills-position-store]] ingests — a renamed field breaks the test loudly.
- **No broker, no credential:** AST-level assertion mirroring `test_order_ticket.py` — the module
  imports no broker/submit symbol and reads no credential/env token.
- **Unresolvable cell is a labelled failure:** a grid cell with no matching contract in the as-of
  chain raises a labelled error (never a silent default, never a bare exception).

## Depends on / pairs with
[[execution-order-ticket]] (landed — produces the abstract ticket) → **this** (resolve + price) →
[[execution-booking-commit]] (§7 #1 — synthesizes the concrete fill from the resolved+marked leg) →
[[execution-fills-position-store]] (ingests the fill; risk/attribution read it). **Blocks
[[execution-booking-commit]] until the fork above is ruled.** Read `tasks/TESTING.md` and the
`check-lookahead-bias` skill.
