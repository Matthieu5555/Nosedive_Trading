# 3A — Order ticket: construct a ticket from a basket (read-only / paper, gated)

> **This is the *début de page 3* (Fri 2026-06-12) — a deliberately minimal, SAFE beginning.**
> Build the **ticket model** (from a 2A basket: legs, qty, side, market/limit, the target broker
> via the **existing adapter seam**) plus a ticket-construction/**preview** UI. **Transmission stays
> disabled.** No live orders, no credentials in the app, paper/read-only by default. Sending an order
> is **3B** and lives behind an explicit, separate owner gate — not here.

- **Owns:** a new **ticket model** on the analytics/contracts side
  (`packages/infra/src/algotrading/infra/orders/` — ticket, leg, side, time-in-force, the
  market/limit price spec, and the *pure* basket→ticket builder), a **ticket-preview BFF router**
  (`apps/frontend/src/algotrading/frontend/routers/ticket.py` + its serializer in `serializers.py`),
  and the **ticket-construction/preview UI** (`apps/frontend/web/src/`: a Ticket panel + its typed
  client in `api.ts`). The target broker is named only as a value that resolves to **one of the
  IBKR leaf adapter** (`packages/infra-ibkr`) through the broker seam
  (`packages/infra/src/algotrading/infra/connectivity/session.py`, `BrokerTransport`). Conforms to
  **[ADR 0011](../.agent/decisions/0011-blueprint-as-plan-of-record.md)** (blueprint governs the
  order/ticket domain — leg semantics, side/qty conventions, the price spec are its call, not ours),
  **[ADR 0023](../.agent/decisions/0023-nautilus-runtime-spine-and-library-leverage.md)/[0024](../.agent/decisions/0024-ibkr-rest-transport-alongside-tws.md)/[0025](../.agent/decisions/0025-nautilus-host-catalog-topology.md)**
  (Nautilus is the runtime spine, broker reached only through the adapter seam + IBKR transport), and
  **[ADR 0030](../.agent/decisions/0030-frontend-visualization-and-ui-library-stack.md)** (shadcn/ui
  for the shell).
- **Depends on:** **2A** (`tasks/2A-basket-builder.md`) — the basket the ticket is built *from*; the
  ticket model takes a 2A basket as input and must use its leg/instrument identity
  (`contracts/instrument_key.py`, `InstrumentKey`), not a parallel shape. No ticket without a basket.
- **Blocks:** **3B** (`tasks/3B-sign-and-send.md`) — sign + send. 3B opens the owner gate, extends the
  broker seam with an order-submission verb, and is the *only* task that may transmit. Keep 3A's model
  the thing 3B signs; do not pre-build submission here.
- **State going in (verified 2026-06-07):** the broker leaf adapters exist
  (`packages/infra-ibkr`), reached through the `BrokerTransport` **Protocol** seam in
  `connectivity/session.py` — which today owns connection lifecycle only (`open`/`close`/
  `current_time`); **there is no order-submission verb on the seam** (that is 3B's to add). The Codex
  `market`/`orders` paper-trading routers were **deleted in C4** (≈700 lines of fixtures, no backend
  equivalent) — **do not resurrect `/api/orders` or `/api/market`, and do not build the ticket over
  fixtures.** The BFF (`app.py`) wires real store-backed routers only; `serializers.py` is the
  serializer home; `api.ts` mirrors it (the HTTP shape is the seam). There is **no** order/ticket
  contract today; `risk/basket.py` is a variance computation, **not** the 2A UI basket — do not
  overload it.

## Objective

An operator who has built a basket in 2A can **construct an order ticket from it** — each basket leg
becomes a ticket leg with an explicit **side** (buy/sell), **quantity**, and **price spec**
(market, or limit with a price), targeting a named broker that resolves to the **IBKR leaf
adapter**. The operator **previews** the fully-built ticket (legs, per-leg side/qty/price, the
resolved target broker, an aggregate summary) in the UI. **Nothing is transmitted.** The ticket model
is a typed, validated, serializable contract — the object 3B will later sign and send — and the
build step is a **pure** basket→ticket function with no I/O, no credentials, no network. Paper /
read-only is the default and the only mode 3A ships; the live path does not exist until 3B's explicit
owner gate.

## What to do (ordered)

1. **The ticket model (contract, pure).** Add `packages/infra/src/algotrading/infra/orders/` with a
   frozen, typed **`OrderTicket`** carrying: an ordered list of **`TicketLeg`** (each = an
   `InstrumentKey` from `contracts/instrument_key.py`, a **`Side`** enum buy/sell, a positive
   **quantity**, and a **price spec**), a **`PriceSpec`** that is either `Market` or `Limit(price)`
   (model the two as a closed set — a limit with no price, or a market with a price, is invalid by
   construction), a **time-in-force** enum, the **target broker** as an enum/identifier that resolves
   to `ibkr` (the sole live broker today; kept an enum so another broker can rejoin), and a provenance/source reference back to the originating
   basket. **The blueprint (ADR 0011) governs leg semantics, side/qty conventions, and the price spec
   — read it; do not invent field names or conventions the blueprint already fixes.** Add a `__mode__`
   / explicit flag pinning the ticket as **paper/read-only** — transmission is structurally absent
   from this module.
2. **The pure builder.** `build_ticket(basket, *, side_by_leg | default_side, price_spec, broker,
   tif) -> OrderTicket`: a **pure function**, no I/O, that maps a 2A basket to a validated
   `OrderTicket`. It validates *at build time* — every leg has a positive qty, a resolved
   `InstrumentKey`, a coherent price spec; the target broker resolves to an existing adapter; an empty
   basket, a zero/negative qty, a duplicate leg, or an unknown broker raise a **labeled** error
   (named `TicketError(reason, …)` or similar), never a silent default and never a bare
   `ValueError`/`KeyError`. **No credentials, no network, no adapter call** — the builder names the
   broker, it does not connect to it.
3. **Resolve the broker through the existing seam — name only, do not transmit.** The target-broker
   value maps to one of the leaf adapters (`packages/infra-ibkr`) via the established
   selection seam, *only* to (a) validate the broker is real and (b) carry the identifier on the
   ticket. **Do not add an order-submission method to `BrokerTransport`** and **do not call any
   adapter** — that verb and that call are 3B's, behind the gate. If the natural seam touchpoint is
   missing, stop at naming/validation; leave a one-line `# 3B:` marker, do not build the path.
4. **Ticket-preview BFF router (read-only).** Add
   `apps/frontend/src/algotrading/frontend/routers/ticket.py` exposing a **build/preview** endpoint
   (`POST /api/ticket/preview` — request = a basket ref + the build params; response = the serialized
   built ticket) that calls the **pure builder** and serializes the result. **No write, no store
   mutation, no broker call, no `/api/orders`.** Add a `ticket_to_dict` serializer beside the others
   in `serializers.py`; register the router in `app.py` (CORS already covers it). A malformed
   request → a **labeled 400** (mirror the existing routers' bad-input behaviour); never a 500, never
   a silent coercion.
5. **Typed client + preview UI (web).** Extend `api.ts` with `OrderTicket`/`TicketLeg`/`PriceSpec`/
   `TicketPreviewRequest` interfaces mirroring the serializer (keep them in sync — the header comment
   makes the HTTP shape the seam). Add a **Ticket panel** (shadcn/ui per ADR 0030) reached from the
   2A basket view: per-leg side toggle + qty input + market/limit price spec, a target-broker picker
   (IBKR — the sole adapter today), and a **read-only preview** of the fully-built ticket (legs, side/qty/price,
   resolved broker, an aggregate summary). The **transmit/send affordance is absent or visibly
   disabled with a "3B — gated" label** — there is no code path from this UI to a broker. Every panel
   self-labels (what am I looking at?).
6. **Make the gate explicit in prose + code.** A short module docstring and a one-line UI label state:
   3A is **preview/build only, paper/read-only, no transmission**; sending is 3B behind an explicit
   owner gate. No credential is read, no broker is contacted, anywhere in 3A.

## Test surface

Read `tasks/TESTING.md`. The pure model/builder is covered by the root Python gate; the BFF router by
the same gate; the web panel by `apps/frontend/web`'s `npm run lint && npm test`. Specific named
cases:

- **Build round-trip (pure, independent oracle = a hand-built basket):** a 2A basket with hand-chosen
  legs builds into an `OrderTicket` whose legs, sides, qtys, price specs and target broker equal the
  values **derived by hand in the test** — `test_build_ticket_maps_basket_legs_one_to_one`. Each leg's
  `InstrumentKey` is the basket's, not a re-parsed parallel shape.
- **Validation / labeled failure (negative paths are first-class):** empty basket, zero/negative qty,
  a `Limit` with no price, a `Market` carrying a price, a duplicate leg, and an unknown target broker
  each raise the **labeled `TicketError`** carrying the offending value — never a bare exception, never
  a silent default (`test_build_ticket_rejects_*`, one per case).
- **Contract round-trip + serializer seam (mirror `test_readback_api.py`'s discipline):** an
  `OrderTicket` serializes via `ticket_to_dict` and the payload exposes the blueprint/ADR-0011 field
  names; a renamed contract field turns the assertion red
  (`test_ticket_payload_uses_blueprint_field_names`). The BFF preview endpoint returns the **same**
  ticket the pure builder returns for the same input (`test_ticket_preview_matches_pure_builder`).
- **No transmission, no credentials — the safety invariant:** assert, by construction, that the orders
  module and the ticket router import **no** adapter-submission symbol and read **no** credential/env
  token; assert `BrokerTransport` gains **no** submit/place verb in 3A
  (`test_ticket_path_never_transmits`, `test_ticket_path_reads_no_credentials`). This is the gate, made
  a test.
- **Broker resolves to a real adapter, names only:** a valid target resolves to one of
  `ibkr` (the sole live broker today); an unknown one is the labeled failure above
  (`test_target_broker_resolves_to_existing_adapter`).
- **Edge / boundary (the floor):** single-leg basket, duplicate legs, qty exactly at the boundary,
  empty basket → each handled per the named rule above, not a crash.
- **Web component (Vitest + Testing Library, alongside `Risk.test.tsx`/`Surfaces.test.tsx`):** the
  Ticket panel renders a built ticket from a mocked `/api/ticket/preview` (do not hit a live BFF),
  shows per-leg side/qty/price + the resolved broker + the self-labels, and the **send affordance is
  absent or disabled** with the 3B-gated label visible (`Ticket.test.tsx`); a preview fetch error
  renders through `AsyncBlock`, not a blank page. Assert user-facing text per the write-tests UI rule.

## Done criteria

A typed, validated, serializable **`OrderTicket`** model + a **pure** `build_ticket(basket, …)`
exist under `packages/infra/.../orders/`, taking a 2A basket and producing legs with explicit
side/qty/price spec and a target broker that resolves to an **existing** leaf adapter — every invalid
construction a **labeled** failure. A **read-only** `POST /api/ticket/preview` router (registered in
`app.py`, serialized in `serializers.py`, mirrored in `api.ts`) previews the built ticket; a
shadcn Ticket panel builds and previews it from the 2A basket with the **send path absent/disabled and
labeled 3B-gated**. **Nothing transmits; no credential is read; `BrokerTransport` gains no submit
verb; `/api/orders` is not resurrected.** Both gates green: the root Python gate
(`ruff && mypy && lint-imports && pytest`) and, in `apps/frontend/web`, `npm run lint && npm test`.

## Gotchas

- **This is a SAFE beginning — preview/build only.** The single most important property of 3A is that
  it **cannot transmit**: no order-submission verb on the seam, no adapter call, no credential read.
  The `test_ticket_path_never_transmits` / `…reads_no_credentials` tests are the gate made falsifiable
  — if either can pass while a send path exists, the test is wrong.
- **Sending is 3B, behind an explicit owner gate.** Do not pre-build submission "to save 3B time".
  The owner gate is **explicit and separate**; the ticket is the object 3B signs — keep it clean and
  inert.
- **Route through the existing broker seam, never a new ad-hoc path.** The broker is reached only via
  the `BrokerTransport` seam + the leaf adapters (`packages/infra-ibkr`); in 3A you
  only *name and validate* the target, you do not connect. Leave a `# 3B:` marker where the submit
  verb will attach; do not add it.
- **Do not resurrect the deleted code.** `/api/orders`, `/api/market`, and `store_serving.py` were
  removed in C4 because they were 100% fixtures. Build a **real** ticket model over the adapter seam,
  not a fixture router. If a basket source is empty, return a labeled-empty/400, do not synthesize.
- **The blueprint (ADR 0011) overrides on the order domain.** Leg semantics, side/qty conventions,
  the market/limit price spec, time-in-force values — these are the blueprint's calls. Read it; do not
  invent names or conventions it already fixes, and do not redefine them in the serializer.
- **The basket is 2A's, the leg identity is `InstrumentKey`.** Build from the 2A basket shape; reuse
  `contracts/instrument_key.py` for leg identity — do not parse a parallel instrument shape and do not
  overload `risk/basket.py` (that is variance math, not the UI basket).
- **The HTTP shape is the seam** (the `api.ts` header comment). A serializer change without the
  matching `api.ts` change is silent drift — keep them in lockstep.
- **Phase 3 is parallel-OK, but the front (1I) is the priority.** This is a thin, safe slice; do not
  let it grow. **uv** for the Python model + BFF; **npm** for the web panel.
