# T-fills-position-store — the book built from fills, read by risk/attribution (week, §7.1 write side)

> **Source:** TARGET §5.1 + §5.5 + §7.1. Today a "position" is a **composed basket**, not a
> booking result — §5.1 flags "a position store fed by fills" as the missing piece. Neither 3A
> (ticket) nor 3B (sign/send) specs it.

## The gap
The booking chain (`order → fill → position → risk → P&L`) has no **position store fed by
fills**. `packages/execution` is empty. The risk/attribution engines read composed baskets, not
booked positions.

## Scope (the STORE + its read-wiring — the booking-commit verb is split out)
- A **fills-based position store**: a position is the running result of fills (signed, with
  lineage to the order/ticket that created it), never an intention. Partial fills accumulate into
  the running position; the store is the §6 "accounting from fills, not orders" source of truth.
- Wire the risk + attribution engines to read the **booked** position store (alongside / instead
  of the composed-basket path) so the §5.1/§5.2 chain exists end to end in paper form.
- Index-only, IBKR sole broker, SX5E sole live index (TARGET §0 / ADR 0042). Paper/read-only
  against the broker until 3B's owner gate opens.

**Out of this slice (split out, week #1 priority):** the **password-gated booking commit** — the
write barrier that turns a previewed 3A ticket into the fill(s) this store ingests — now lives in
[[execution-booking-commit]]. This task owns the *store and its readers*; that task owns the
*gated write path* into it. The seam between them is the fill record + position-write API.

## Depends on / pairs with
[[execution-booking-commit]] (the gated write path that feeds this store) ← [[execution-order-ticket]]
(the ticket booked) ← [[execution-order-sign-and-send]] (the broker-send owner gate, separate, off
this week). Accounting from fills, not orders (§6).

## Done criteria
A booked fill (written via [[execution-booking-commit]]) becomes a fills-based position; risk +
attribution read it; partial fills accumulate; order/ticket lineage on every position; the store is
append-only/auditable; gate green.
