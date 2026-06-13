# T-fills-position-store — the book built from fills, read by risk/attribution (week, §7.1 write side)

> **Source:** TARGET §5.1 + §5.5 + §7.1. Today a "position" is a **composed basket**, not a
> booking result — §5.1 flags "a position store fed by fills" as the missing piece. Neither 3A
> (ticket) nor 3B (sign/send) specs it.

## The gap
The booking chain (`order → fill → position → risk → P&L`) has no **position store fed by
fills**. `packages/execution` is empty. The risk/attribution engines read composed baskets, not
booked positions.

## Scope
- A **fills-based position store**: a position is the running result of fills (signed, with
  lineage to the order that created it), never an intention. Partial fills accumulate.
- The **password gate** is the book's write barrier (TARGET §2 #4): nothing changes the book
  without an explicit human gate. Paper/read-only against the broker until 3B's owner gate.
- Wire the risk + attribution engines to read the **booked** position store (alongside / instead
  of the composed-basket path) so the §5.1/§5.2 chain exists end to end in paper form.

## Depends on / pairs with
[[3A-order-ticket]] (ticket → booking) + [[3B-order-sign-and-send]] (the owner gate). Accounting
from fills, not orders (§6).

## Done criteria
A password-gated booking writes a fills-based position; risk + attribution read it; partial fills
+ lineage handled; append-only audit of book writes; gate green.
