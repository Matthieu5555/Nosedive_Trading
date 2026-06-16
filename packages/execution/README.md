# execution

The booking chain above `infra` — an upper layer in the import stack
(`core ← infra ← infra-<broker> ← {strategy, execution} ← frontend`). It imports
`infra`/`core` only and is never imported by them.

**Paper, read-only.** Nothing here transmits an order, reads a credential, or connects to a
broker. The password-gated *booking commit* (which mints fills) and the live broker *send*
gate (3B) are two separate, later barriers — neither lives in this package.

## The fills-based position store (TARGET §5.1 / §5.5 / §6 / §7.1)

The book is accounted **from fills, never from intentions**. The pieces:

- **`Fill`** (`fills.py`) — one execution of one *concrete* contract (`contract_key`), carrying
  a signed `Decimal` quantity, lineage back to the booking decision (`booking_id`) and the
  originating 2A basket (`source_basket_id`), and a `ProvenanceStamp`. Paper-only by
  construction.
- **`FillsLedger`** (`ledger.py`) — the **append-only, auditable** source of record. A fill,
  once appended, is immutable; a duplicate `fill_id` is rejected; there is no mutate/delete
  verb. Two implementations: `InMemoryFillsLedger` (working store) and `JsonlFillsLedger`
  (durable — a file that only ever grows and replays on restart). The provenance stamp is
  validated at the append door.
- **`position_set_from_fills` / `booked_position_set`** (`book.py`) — fold the ledger into the
  `PositionSet` the risk engine already reads (`build_risk_snapshot`), `source="booked"`.
  Partial fills accumulate exactly; a net-zero contract is a closed position (gone from the
  live book, kept in the ledger). No risk code is touched — the engine was already agnostic to
  where its book came from; this is the source `risk.positions.hypothetical_positions` flagged
  as "the seam a live broker-positions source will later mirror".

## Fill concretization (ADR 0043) — grid cell → concrete priced fill

`concretization.py` is the transform WS 3A deferred and the booking commit consumes: from an
abstract grid-cell [order-ticket leg](../infra/src/algotrading/infra/orders/README.md)
(`underlying, tenor_label, delta_band`) to a concrete, priced **`ConcreteFill`**
(`contract_key`, `(strike, expiry, right)`, a paper `fill_price`). It is ruled by
[ADR 0043](../../.agent/decisions/0043-fills-are-concrete-contracts-resolved-at-booking.md):
*a booked fill is a concrete contract, resolved at booking time.*

- **`concretize(ticket_leg, as_of, chain)`** — a **pure, as-of** resolver: no I/O, no clock, no
  broker, no credential. `chain` (`ConcreteChain`) is the captured chain + marks read as-of the
  booking date, so it is the *only* source of strikes, expiries and prices — an old-date replay
  resolves that date's contract, never today's (the look-ahead guard is structural).
- **Resolution.** The grid cell is matched to its WS-1F `ProjectedOptionAnalytics` row (the same
  `analytics_cell_key` the risk engine uses — one join key, not a parallel one), which carries
  the solved `strike` and `target_delta`. The option right comes from the band via
  `option_right_for_band` (the public twin of `surfaces.projection._option_right_for_band`,
  pinned equal by a test). The `(strike, right)` is then bound to a real listed contract off the
  chain at the **soonest listed expiry on/after `as_of`** — the same `contract_key` a live
  broker-send would bind (ADR 0043: no re-keying at the live boundary).
- **Paper mark (the pinned rule).** `fill_price` is the **mid of the as-of `MarketStateSnapshot`**
  (`(bid + ask) / 2`) for the resolved contract when a finite two-sided positive quote exists,
  else the analytics row's model `price`. The rule that set it is recorded on
  `ConcreteFill.mark_source` (`snapshot_mid` / `analytics_model_price`). Never a wall-clock read,
  never a silent zero.
- **Labelled failures.** Every unresolvable step raises a `ConcretizationError` carrying the
  offending grid cell and a machine-readable reason (`no_analytics_row`, `provider_ambiguous`,
  `no_listed_contract`, `strike_ambiguous`, `no_mark`, `not_an_option_leg`) — never a default.

`ConcreteFill` is the seam the booking commit consumes: its `contract_key` / `fill_price` /
`instrument.broker_contract_id` map straight onto `Fill.contract_key` / `Fill.price` /
`Fill.broker_contract_id`, and `side` + `quantity` give the signed `Fill.signed_qty` (the commit
applies the sign). One source, not three — a renamed field breaks the seam round-trip test.

## The password-gated booking commit (`booking/`, TARGET §2 #4 / §7 #1)

The **write barrier**: the one verb that mutates the book, behind a password. It turns a
previewed [3A ticket](../../packages/infra/src/algotrading/infra/orders/) into the fills the
store above ingests — but only when the gate verifies. Fail-closed by construction.

- **`book(ticket, password, *, ledger, audit_log, resolver, chain, now, booking_id, …)`**
  (`booking/commit.py`) — verifies the gate, on success resolves each ticket leg to a concrete
  priced `Fill` and appends it **once** to the `FillsLedger`, and appends **one**
  `BookingAudit` record (commit *or* block) to the audit log. Returns `BookingCommitted` or a
  labelled `BookingBlocked`. On any block — wrong/absent password, unconfigured/malformed gate,
  or an unresolvable leg — **no fill is written** (`ledger.append` is never called) and the
  block is still recorded. Pure/DI: ledger, audit log, resolver, chain, clock, ids all injected.
- **The password gate** (`booking/password_gate.py`) — `verify_password` hashes with
  `hashlib.scrypt` and compares with `secrets.compare_digest` (constant-time) against a stored
  salt + digest read from the environment (`$HOME/.env`: `BOOKING_GATE_SCRYPT_SALT` /
  `BOOKING_GATE_SCRYPT_HASH`). No plaintext, no homegrown crypto, no `.py` literal. `hash_password`
  is the provisioning helper an operator runs once to mint the stored digest. This is the **paper
  booking** gate, **not** the 3B broker-send gate — two different gates.
- **The booking audit log** (`booking/audit.py`) — `BookingAudit` + an **append-only**
  `BookingAuditLog` (`InMemory…` / `Jsonl…`, mirroring the fills ledger): every commit/block is
  one immutable, provenance-stamped record; no mutate/delete verb; duplicate `audit_id` rejected;
  the stamp is order-independent so a replay of the decision sequence is reorder-stable (§6).
- **The concretization seam** (`booking/concretization_seam.py`) — `ResolvedLeg` + the
  `LegResolver` protocol the commit calls (ADR 0043: a booked fill is a concrete contract
  resolved as-of the booking date). The pure resolver itself is owned by the
  `execution-fill-concretization` task (built in parallel, not yet merged); this module is the
  commit's *interface view* of it, so the commit depends on the shape, not the
  parallel module's code. **Wire-on-merge:** point the BFF's `_resolver_for` at the real resolver
  when it lands — no commit change needed.

### Provisioning the gate

```
uv run python -c "import secrets; from algotrading.execution.booking import hash_password; \
  salt = secrets.token_bytes(16); \
  print('BOOKING_GATE_SCRYPT_SALT=' + salt.hex()); \
  print('BOOKING_GATE_SCRYPT_HASH=' + hash_password('your-password', salt))"
```

Place both lines in `$HOME/.env` (gitignored). The password itself never leaves your shell.

The BFF exposes this as `POST /api/booking/commit` (the ticket-preview body plus a `password`);
the React Ticket panel adds the password prompt + **Book (paper)** affordance. The 3B sign-and-send
affordance stays disabled and labelled. **No broker bytes leave the process.**

## Read side — the fills ledger and the booked book over HTTP

The same `JsonlFillsLedger` the commit writes is read back by the BFF for the Positions/Execution
blotter: `GET /api/positions/fills` projects the append-only ledger verbatim, and `GET /api/positions`
folds it via `booked_position_set` and joins each `contract_key` to the latest banked
`pricing_results` row to attach per-leg Greeks (`raw × signed_qty × multiplier`, dollar-Greek ×
`signed_qty`) and a book-additive total. Accounting is from fills, marks are the as-of banked
pricing, and the store opens read-only — no fill is written and no broker is touched on the read
path. The endpoint shapes live in `apps/frontend/README.md`.
