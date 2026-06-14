# frontend-orders-booking-reconcile — one coherent booking chain; retire the dead Orders sketch

> **A delivery-coherence gap no spec catches.** The real, store-backed order ticket (3A,
> [execution-order-ticket](archive/execution-order-ticket.md)) landed on the **Basket** page
> (`TicketPanel` at `Basket.tsx:157`, fed by `POST /api/ticket/preview`). The **Orders** tab
> (`pages/Orders.tsx`, wired in `App.tsx`) is still the old read-only *sketch*: hardcoded
> fields (`strike: 5350`, `limit_price: 47.5`), a client-side notional, a disabled "Submit
> (sketch)", and no BFF call except `/api/indices`. So the booking chain TARGET wants as
> *one chain* is split — a real preview on Basket, a dead duplicate on Orders — and
> [execution-booking-commit](execution-booking-commit.md) plans to bolt its password prompt
> onto the Basket `TicketPanel`, which would leave the Orders tab permanently stale.

## Why (TARGET cite)
TARGET §2 #3-4 — "Enter a strategy … book it, and hold it as *the current position*" and
"An order booking system that works. Ticket → confirm → booked position, **as one chain**."
§7 #1 ranks the booking chain the week's number-one item. A dead sketch tab beside the real
flow is exactly the "dead buttons / mock data" the §2 clean-frontend goal forbids ("every
panel answers what am I looking at, wired to the real pipeline; no mock data, no dead
buttons").

## Scope boundary
- **In:** decide and implement **where the booking chain lives**, then make the front
  coherent — one of:
  - **(a)** Move the real `TicketPanel` (preview) **to the Orders tab** as its home, leaving
    Basket to compose+price+stress and "send to ticket"; Orders becomes ticket → (password)
    confirm → booked history. *Or*
  - **(b)** Keep the ticket on Basket and **retire the Orders tab** (remove the route +
    sketch, or repoint it to the real flow) so there is no duplicate.
  Either way: kill the hardcoded sketch (`strike: 5350` et al.), and leave a single, real,
  self-labelled booking surface. The chosen home is where
  [execution-booking-commit](execution-booking-commit.md)'s password-prompt + confirm and the
  booked-position/history view mount.
- **Out:** the booking-commit *verb*, the password gate, the fills store, and the audit log
  — all [execution-booking-commit](execution-booking-commit.md) / [execution-fills-position-store](execution-fills-position-store.md).
  This task owns **the front coherence + placement**, not the write barrier. No broker send
  (3B, [execution-order-sign-and-send](execution-order-sign-and-send.md)) — the send
  affordance stays absent/disabled and labelled 3B-gated. Do **not** resurrect `/api/orders`
  or `/api/market` (deleted in C4 — 700 lines of fixtures).

## Decision needed first
This is a **placement ruling** (owner / front-lane call): (a) vs (b). Record it in the spec
before building — and coordinate with [execution-booking-commit](execution-booking-commit.md),
which currently *assumes* the prompt mounts on the Basket `TicketPanel`. The two specs must
agree on the one home so the password flow and this reconciliation don't cross.

## Dependencies / coordination
- The real ticket flow (3A) is landed (`TicketPanel`, `routers/ticket.py`, `api.ts`).
- Shared-tree hazard: `App.tsx`, `pages/Orders.tsx`, `pages/Basket.tsx` overlap the live
  front lane (`frontend-page1-cdc-buildout` reflows Market, the anthony Basket/Risk lane,
  [execution-booking-commit](execution-booking-commit.md) edits the ticket panel). Claim the
  file rows and serialize.
- E2E: the navigation/layout Playwright suite (`npm run e2e`) covers the tabs — update it for
  the moved/retired route so a removed tab or a relocated panel stays green.

## Test surface
Read `tasks/TESTING.md`.
- **Component (Vitest + RTL).** The chosen single booking home renders the real ticket
  (mocked `/api/ticket/preview`), self-labels, and shows the send affordance disabled +
  3B-gated; the duplicate is gone (assert the old hardcoded sketch text / `strike 5350` no
  longer renders anywhere).
- **Routing.** If a tab is retired, its route redirects (no dead link); if moved, the nav and
  `App.tsx` point at the real flow. Assert via the router test.
- **E2E (Playwright).** Navigation across the reconciled tabs is green; no layout collision /
  overflow on the booking home.
- Web gate green (`npm run lint && npm test`); Python BFF tests green if a router moves.

## Done criteria
There is **exactly one** real, store-backed booking surface on the front; the hardcoded
Orders sketch is gone (moved or retired, route coherent); the chosen home is the agreed mount
for [execution-booking-commit](execution-booking-commit.md)'s password flow; the send path
stays absent/disabled + 3B-gated; component + router tests and the e2e navigation suite green;
no `/api/orders` resurrection, no mock data, no dead buttons.

## Gotchas
- **Pick one home and write the ruling down** before editing — (a) or (b). The whole point is
  to *remove* the duplication, not add a third surface.
- **Coordinate with the booking-commit lane** — they target the Basket `TicketPanel` today;
  if this task moves the ticket to Orders, that spec's mount moves with it.
- **No fixtures, no dead buttons** (§2). The retired sketch's hardcoded numbers are the exact
  anti-pattern; do not leave a "demo" ticket behind.
- **uv** for any Python/BFF touch, **npm** for the web; stage by explicit path (shared tree).
