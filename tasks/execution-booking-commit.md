# execution-booking-commit — the password-gated booking write barrier (week, §7 #1 — TOP priority)

> **Source:** TARGET §2 #4 + §5.5 + §7 #1 (this week's number-one item) + §6 ("accounting from
> fills, not orders", append-only audit log). This is the **connective link** of the booking chain:
> 3A builds/previews a ticket (no write), the fills store holds positions, and **this** is the
> single gated step that commits a previewed ticket into a fill the store ingests. Carved out of
> [[execution-fills-position-store]] because §7 ranks the booking chain #1 and the *write barrier* is
> the sharp, safety-critical slice that deserves its own spec.

## Objective
An operator who has previewed a ticket in 3A can **book it** — turn it into one or more **fills**
that become *the current position of the book* — but only through an explicit **password gate**.
The password is the book's **write barrier** (TARGET §2 #4): nothing mutates the book without a
human supplying the gate secret at commit time. The commit is **paper / read-only against the
broker** — no bytes leave the process; booking simulates the fill(s) and writes them to the store.
Live broker transmission is a **separate, later** gate ([[execution-order-sign-and-send]], 3B),
explicitly off this week. The booking commit and the broker-send gate are **two different gates** —
do not conflate them.

## Scope boundaries
- **Owns:** the **booking-commit verb** in `packages/execution/src/algotrading/execution/` — a pure
  decision/commit function `book(previewed_ticket, password, store, now) -> BookingResult` that
  (a) verifies the password gate, (b) on success synthesizes the paper fill(s) from the ticket
  (full-fill in v1; partial-fill shape supported by the fill record), (c) writes them to the
  fills-position store with **order/ticket lineage**, and (d) appends a provenance-stamped record of
  the decision to an **append-only audit log**. Plus the BFF commit endpoint
  (`apps/frontend/.../routers/` + serializer) and the password-prompt + confirm affordance on the
  3A Ticket panel.
- **The fill record contract** (signed qty, price, ticket/order lineage, paper flag, timestamp) —
  the seam this task produces and [[execution-fills-position-store]] consumes. Co-design the field
  names with that task; one source, not two parallel shapes.
- **NOT this task:** the position store itself and the risk/attribution read-wiring
  ([[execution-fills-position-store]]); the 3A ticket model/preview ([[execution-order-ticket]]);
  any **broker** send path or order-submit seam verb ([[execution-order-sign-and-send]] / 3B — that
  is the *other* gate and stays off). No `BrokerTransport` submit verb is added here.
- Index-only, IBKR sole broker, SX5E sole live index (TARGET §0 / ADR 0042). Password material from
  `$HOME/.env` (gitignored), never a `.py` literal, never the app (AGENTS.md §95–96).

## Why (TARGET cites)
- §2 #4: "Booking a position requires a **password** — an explicit human gate in front of anything
  that changes the book." This task **is** that gate.
- §7 #1: "3A ticket + **password-gated booking** → a fills-based position store … The password gate
  is the book's write barrier." Ranked first.
- §6: accounting is from **fills**, with an **append-only audit log** — the commit emits fills and
  logs the decision; it never writes a position from an intention.

## Done criteria
A previewed 3A ticket commits to fill(s) **only** when the password gate verifies; a wrong/absent
password is a **labeled** block (no store write, no fill) — fail-closed. A successful commit writes
signed fill(s) with ticket/order lineage into the fills store (paper-flagged), and appends a
provenance-stamped, append-only audit record of every commit/block decision (replay reorder-stable).
**No broker bytes** leave the process and `BrokerTransport` gains no submit verb. The 3A panel gains
a password-prompt + confirm that drives this commit; the live-send affordance stays absent/disabled
and labeled 3B-gated. Root gate green (`ruff && mypy && lint-imports && pytest`) + web
`npm run lint && npm test`.

## Test surface (named)
- **Fail-closed gate:** wrong password, absent password, malformed gate config → labeled block, the
  store write method is **never invoked** (assert the absence of the call, not just the enum).
- **Happy path (paper):** correct password → fill(s) synthesized from the ticket, written once, with
  ticket/order lineage equal to the previewing ticket's (independent oracle = hand-built ticket).
- **Two-gates separation:** the booking password is **not** the 3B broker-send gate — a booking
  commit never opens a broker path; assert no order-submit symbol is importable from this module.
- **Audit append-only + provenance:** every commit/block writes one stamped record; mutate/delete of
  a prior record fails; replay reconstructs the decision sequence reorder-stably (subprocess hash
  stability per TESTING.md).
- **Partial-fill shape:** the fill record represents a partial fill without loss (qty < ticket qty
  accumulates in the store) even if v1 only synthesizes full fills.

## Depends on / pairs with
[[execution-order-ticket]] (the previewed ticket booked) → **this** → [[execution-fills-position-store]]
(ingests the fills). Sibling, separate gate: [[execution-order-sign-and-send]] (3B broker send — off
this week). Read `tasks/TESTING.md`.
