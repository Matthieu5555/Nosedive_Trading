# 3B — Order signing (email) + send: gated, paper-first, owner-gated transmission

> **DANGER WORKSTREAM. Spec conservatively.** This is the only task in the tree that can
> move real money. Default is paper / blocked transmission; live send is a deliberate,
> separately-gated capability, not a flag flip. It does **not** go live this week —
> Friday's target is *start of page 3*, transmission stays off. A passing security-review
> is a hard prerequisite before any real send is wired on.

- **Owns:** the order sign-off + send seam in `packages/execution/src/algotrading/execution/`
  (today: empty package, `__init__.py` only) — an email sign-off step (out-of-band operator
  confirmation of a built 3A ticket via an approval token) and a transmit path that routes a
  signed ticket through the **existing broker leaf seam** (`packages/infra-ibkr` — IBKR is the sole live broker),
  plus the append-only, provenance-stamped audit log of every ticket/decision event. Conforms to
  **[ADR 0024](../.agent/decisions/0024-ibkr-rest-transport-alongside-tws.md) §4** (read-only
  invariant on the ingestion transport — sending is a *separate, gated* capability) and the
  blueprint domain (ADR 0011 overrides on any conflict).
- **Depends on:** **3A** (the order ticket — the typed object this signs and sends; sibling spec,
  not yet landed), **security-review** (the gate prerequisite — no live transmission is enabled
  until a security pass is green), C7 (`broker.yaml` / `BrokerConfig` for the gate flag), the
  broker leaf adapters (the seam the send routes through, already present).
- **Blocks:** nothing downstream this week by design — transmission is off. Unblocks a *future*
  live-trading increment once security-review passes and the owner sets the gate.
- **State going in (audited 2026-06-07):** `packages/execution` is an empty package
  (`__init__.py` only) — green field. The broker leaves expose **ingestion only**
  (`collectors/` + `connectivity/` transports); none expose an order/submit method, and ADR 0024
  §4 records the read-only invariant explicitly ("REST order endpoints never called"). The
  canonical `/api/orders` route was **deleted in C4** — it does not exist in `apps/`; the only
  `/api/orders*` handler is Test Lenny's (`Test Lenny/app/server.py`, `IBKR_ENABLE_ORDERS`
  default `false`), which is the **reference for blocked-by-default transmission but is
  non-canonical — ignore it as code**. Secrets convention is fixed: no credentials in the app,
  per-person tokens in `$HOME`, project config in a gitignored `.env` (AGENTS.md §95–96).

## Objective

A built 3A ticket can be (1) **signed off** out-of-band by the operator via an email approval
token, and (2) **transmitted** through the existing broker leaf seam — but transmission is
**disabled by default** and only fires when **both** gates are open: an explicit owner gate
(env/config flag) **and** a valid, unexpired email confirmation matching that exact ticket.
Anything short of both → **paper / blocked**: the ticket is recorded, the decision is logged, no
order leaves the process. Every ticket and every decision (built, signed, gate-checked, sent,
blocked, rejected) is written to an **append-only, provenance-stamped audit log** — the full
record of what was asked, what was approved, and what actually transmitted.

## What to do (ordered)

1. **Define the signed-ticket + decision contracts.** A `SignedTicket` (the 3A ticket + the
   approval token, the approver identity, an issued-at and an **expiry**, and a binding hash over
   the *exact* ticket fields so a token can never authorize a different ticket) and a
   `TransmissionDecision` enum/record (`BLOCKED_DEFAULT`, `BLOCKED_NO_SIGNOFF`,
   `BLOCKED_GATE_OFF`, `BLOCKED_EXPIRED`, `BLOCKED_TICKET_MISMATCH`, `SENT_PAPER`, `SENT_LIVE`).
   Frozen, provenance-stampable, registered like the other contracts.
2. **Email sign-off step (out-of-band).** Render a built 3A ticket to an approval request and
   verify a returned token against the ticket binding hash, the approver, and the expiry. The
   token proves *a human looked at this exact ticket and said yes*; verification is pure and
   testable offline (no live mailbox in the gate). Keep the channel adapter thin and behind a
   port — the Gmail tool exists but a real send is **not** wired this week.
3. **The owner gate (env/config flag).** A single named flag — `EXECUTION_TRANSMIT_ENABLED`,
   default `false`, read via C7's `BrokerConfig`/`broker.yaml`, secret material from `$HOME/.env`,
   never the app. The flag distinguishes **paper** (default) from **live**; live additionally
   requires the security-review prerequisite to have been recorded as passed. No code path sends
   live with the flag absent or unparseable — fail closed.
4. **The send path through the existing broker seam.** Add an order-submit capability to the
   broker leaf seam (start with one leaf — IBKR, per ADR 0024's separate gated-send framing) as a
   **new, explicit, separate method**, never folded into the read-only ingestion transport. The
   execution module calls the seam; it does not open its own ad-hoc broker connection. Default
   build wires the **paper / blocked** sink: the call is recorded and short-circuited, no bytes to
   the venue.
5. **The transmit decision function — fail-closed, both-gates.** One pure function:
   `(SignedTicket, gate_config, now) -> TransmissionDecision`. It sends live **only** when the
   flag is `live` **and** the sign-off verifies **and** the token is unexpired **and** the binding
   hash matches **and** security-review is recorded green. Every other path returns a `BLOCKED_*`
   decision. No exceptions silently swallowed into a send.
6. **Append-only, provenance-stamped audit log.** Every event — ticket built, sign-off
   requested/received, gate evaluated, decision, transmit attempt + venue ack — appended (never
   updated, never deleted) with a `ProvenanceStamp` (code identity + config hashes, per C7) and a
   monotonic timestamp. The log is the source of truth for "what did we actually send"; it must be
   reconstructable and reorder-stable.

## Test surface

Read [TESTING.md](TESTING.md). The independent oracle for the gate is the **decision table**:
enumerate the full cross-product of (flag ∈ {absent, paper, live}) × (sign-off ∈ {valid, missing,
expired, mismatched-ticket}) × (security-review ∈ {green, not-recorded}) and assert each cell maps
to the *named* `TransmissionDecision` — the table is hand-written in the test, not derived from the
code. Specific cases (name each):

- **Fail-closed default:** flag absent / unparseable → `BLOCKED_DEFAULT`, no seam call. This is
  the floor case — assert the broker submit method is *never invoked*.
- **Both gates required:** valid sign-off but flag `paper` → `SENT_PAPER` (recorded, no venue
  bytes); flag `live` but no/invalid sign-off → `BLOCKED_NO_SIGNOFF`; flag `live` + valid sign-off
  but security-review not recorded green → blocked.
- **Token binds the exact ticket:** a token issued for ticket A presented with ticket B (one field
  perturbed) → `BLOCKED_TICKET_MISMATCH`. Perturb each material field (symbol, side, qty, limit).
- **Expiry boundary:** token at `expiry - ε`, at `expiry` exactly, at `expiry + ε` — assert the
  boundary rule (on-the-second is rejected) per the edge-case checklist.
- **Seam routing, not ad-hoc:** the send goes through the broker leaf seam method; a test pins the
  seam shape so a leaf-side change breaks this suite loudly (seam test, per TESTING.md). The
  read-only ingestion transport is asserted untouched (ADR 0024 §4 invariant holds — REST order
  endpoints still never called on the ingestion path).
- **Audit log append-only + provenance:** every decision writes a stamped record; an attempt to
  mutate/delete a prior record fails; replaying the log reconstructs the decision sequence;
  reorder the recorded events in the test and assert the reconstruction is unchanged. Cross-process
  hash stability on the stamps (subprocess), per TESTING.md.
- **Negative paths are first-class:** malformed token, malformed ticket, gate config with a typo
  value → labeled failure / `BLOCKED_*`, never a crash and never a silent send.
- **Gate green:** `ruff && mypy && lint-imports && pytest`.

## Done criteria

The default build cannot transmit a live order: with the flag absent it is structurally blocked
and the broker submit method is never called. A live send fires **only** behind both gates (owner
flag `live` + verified, unexpired, ticket-bound email sign-off) **and** a recorded-green
security-review; everything else returns a named `BLOCKED_*` decision. The send routes through the
existing broker leaf seam (one leaf wired, IBKR), never a new ad-hoc broker path, and the read-only
ingestion invariant (ADR 0024 §4) is preserved. Every ticket/decision/transmit event is in an
append-only, provenance-stamped audit log that replays reorder-stable. No credentials in the app.
Root gate green. **Transmission ships off this week** — this lands the page-3 scaffold, not a live
trader.

## Gotchas

- **Both gates, fail closed.** The flag alone must never send, and a sign-off alone must never
  send. Default and every error path resolve to blocked, not to "best effort send." Test the
  *absence* of the seam call, not just the returned enum.
- **The token must bind the exact ticket.** A sign-off that authorizes "an order" rather than
  "*this* order (this symbol/side/qty/limit, hashed)" is a replay hole — perturb-a-field is a
  required test, not a nice-to-have.
- **Do not touch the read-only ingestion transport.** Order submit is a *new, separate* seam
  method on the leaf; folding it into the ingestion transport breaks ADR 0024 §4 and the
  broker-free gate (the SDK/transport stays lazily imported so the gate runs broker-free).
- **Test Lenny is a reference, not code.** Its `/api/orders/place` + `IBKR_ENABLE_ORDERS=false`
  pattern is the right *shape* for blocked-by-default; do not import or port it. The canonical
  `/api/orders` route was deleted in C4 — do not resurrect a route; the send seam is in
  `packages/execution`, not the BFF.
- **security-review gates *live*, not the build.** Build the paper/blocked path now; the live
  branch stays unreachable (flag default + no recorded pass) until the security task is green.
  Coordinate the "recorded-green" handshake with that task — don't invent a second source of
  truth for it.
- **No secrets in git or app.** Approval tokens and any broker credential come from `$HOME/.env`
  (gitignored), per AGENTS.md §95–96 — never a `.py` literal, never committed config.
- **Blueprint (ADR 0011) overrides** on any order/ticket/transmission domain detail that conflicts
  with this spec. Cross-ref the 3A sibling (`tasks/3A-order-ticket.md`) for the ticket shape this
  consumes once it lands; align field names with it rather than inventing parallel ones.
