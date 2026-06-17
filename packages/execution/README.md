# execution

The booking chain above `infra` — an upper layer in the import stack
(`core ← infra ← infra-<broker> ← {strategy, execution} ← frontend`). It imports
`infra`/`core` only and is never imported by them.

**Paper by default, fail-closed.** Nothing here transmits a live order or connects to a broker
with the gate absent. Two separate barriers live in this package: the password-gated *booking
commit* (which mints paper fills, `booking/`) and the owner-gated *sign-and-send* path (3B,
`transmit/`). Both default to paper/blocked; the live branch of `transmit/` is structurally
unreachable until an owner flag is set **and** a security review is recorded green.

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

## The owner-gated sign-and-send path (`transmit/`, 3B — gated OFF by default)

The one seam that could ever move real money — and by default it cannot. A built
[3A ticket](../../packages/infra/src/algotrading/infra/orders/) is signed off out-of-band by an
operator and then routed for transmission, but a live order leaves the process **only** behind
two independent gates plus a recorded security review. Anything short of all three resolves to a
named blocked decision. This lands the page-3 scaffold; transmission ships **off**.

- **`SignedTicket` + the binding hash** (`transmit/binding.py`, `transmit/signing.py`) — a 3A
  ticket plus an approval token, the approver, an issued-at and an **expiry**, and a
  `binding_hash` over the *exact* ticket fields (symbol/side/qty/price-spec/tif/legs, canonical
  SHA-256). The token is an HMAC over `(binding_hash, approver, expiry)` keyed by a secret read
  from `$HOME/.env` (`EXECUTION_SIGNOFF_HMAC_KEY`) — never a literal, never committed. A token
  thus proves *a human approved this exact ticket*; presenting it against any perturbed ticket
  fails the binding check. `render_approval_request` produces the offline, channel-agnostic
  approval request behind a `SignoffChannel` port — no live mailbox is wired this week.
- **The owner gate** (`transmit/gate.py`) — one flag, `EXECUTION_TRANSMIT_ENABLED`, read from the
  environment (`$HOME/.env`). Absent/blank → `absent` (fail-closed); unrecognized →
  `GateUnparseable` (fail-closed); `paper`/`live` synonyms map explicitly. Live additionally
  requires `EXECUTION_SECURITY_REVIEW=green` — the owner records this only after the
  [security review](../../tasks/platform-security-review-2026-06-17.md) passes; it is the single source of
  truth for the recorded-green handshake, not a second one.
- **The decision function** (`transmit/decision.py`) — one pure
  `decide_transmission(SignedTicket, gate, now) -> TransmissionDecision`. It returns `SENT_LIVE`
  **only** when the flag is `live` **and** the binding matches **and** the token verifies **and**
  it is unexpired **and** the review is green; every other path is a named `BLOCKED_*`
  (`BLOCKED_DEFAULT`, `BLOCKED_NO_SIGNOFF`, `BLOCKED_GATE_OFF`, `BLOCKED_EXPIRED`,
  `BLOCKED_TICKET_MISMATCH`) or `SENT_PAPER`. On-the-second expiry is rejected.
- **The send path + sinks** (`transmit/send.py`, `transmit/sink.py`, `transmit/live_sink.py`) —
  `transmit(...)` evaluates the gate, records the decision, and routes to a sink. The **default
  sink is `PaperSink`**: it records and short-circuits, no bytes leave the process. The live
  `LiveSubmitSink` is **not** exported from the package surface — it must be imported by explicit
  path and wired with a broker submitter, and it submits only on `SENT_LIVE`. The broker submit
  verb is a **new, separate** method on the IBKR leaf
  (`infra_ibkr.connectivity.CpRestOrderSubmit`), never folded into the read-only ingestion
  transport (ADR 0024 §4 invariant preserved and tested).
- **The transmit audit log** (`transmit/audit.py`) — every event (gate evaluated, decision,
  transmit attempt) is one immutable, provenance-stamped `TransmitAudit` record in an append-only
  log (`InMemory…` / `Jsonl…`); no mutate/delete; duplicate id rejected; `replay` is
  reorder-stable; stamp hashes are stable across processes.

The full decision table — flag × sign-off × security-review — is hand-written as the independent
oracle in `tests/test_transmit_decision.py`; `tests/test_two_gates.py` pins that with the flag
absent the broker submit method is never invoked.

## Read side — the fills ledger and the booked book over HTTP

The same `JsonlFillsLedger` the commit writes is read back by the BFF for the Positions/Execution
blotter: `GET /api/positions/fills` projects the append-only ledger verbatim, and `GET /api/positions`
folds it via `booked_position_set` and joins each `contract_key` to the latest banked
`pricing_results` row to attach per-leg Greeks (`raw × signed_qty × multiplier`, dollar-Greek ×
`signed_qty`) and a book-additive total. Accounting is from fills, marks are the as-of banked
pricing, and the store opens read-only — no fill is written and no broker is touched on the read
path. The endpoint shapes live in `apps/frontend/README.md`.
