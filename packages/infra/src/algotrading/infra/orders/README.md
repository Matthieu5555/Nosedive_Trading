# `orders/` — order tickets (WS 3A)

A typed, validated, **inert** order ticket built *purely* from a 2A
[`Basket`](../contracts/tables.py). Preview/build only — **paper/read-only, no transmission**.

Nothing here connects to a broker, reads a credential, or places an order. Building a ticket is a
**pure** function (no I/O, no clock, no network); the ticket is the object WS 3B will later *sign
and send* behind an explicit owner gate. A `# 3B:` marker sits where concrete-contract resolution
(strike / expiry / broker `conid`) attaches at sign time — it is deliberately absent from this
builder, which would otherwise have to read the chain.

## What it owns

- **`ticket.py`** — the model and the pure builder:
  - `OrderTicket` / `TicketLeg` — the ticket and its legs, mirroring the 2A `BasketLeg` identity
    (an option leg names its grid cell `(underlying, tenor_label, delta_band)`; a stock leg names
    the underlying).
  - `Side` (`BUY`/`SELL`), `PriceSpec` (`Market` | `Limit(price)` — a closed set), `TimeInForce`,
    `TargetBroker` (enum → `ibkr`).
  - `build_ticket(basket, …)` — pure `Basket → OrderTicket`. The basket's `long`/`short` +
    sign-consistent quantity is the single source of direction: `long` opens `BUY`, `short`
    opens `SELL`, and the ticket carries a **positive** magnitude (the side carries direction).
  - `TicketError` — every malformed construction is a labelled rejection (offending
    `field`/`value` + human `reason`), never a bare exception and never a silent default.

## Boundaries

- **Read-only / paper.** No submission verb exists in this package; transmission is structurally
  absent. The target broker is named and validated against the `TargetBroker` enum, never connected.
- **3B owns the send.** Signing, the concrete contract binding, and any broker session live in WS
  3B behind the owner gate — not here.
