# 0047 — The password-gated booking write barrier: scrypt gate + a separate append-only decision log

- **Status:** accepted, 2026-06-14. Implements `tasks/execution-booking-commit.md` (TARGET §2 #4 /
  §5.5 / §6 / §7 #1, the week's top item).
- **Relates to:** [0043](0043-fills-are-concrete-contracts-resolved-at-booking.md) (a booked fill is
  a concrete contract resolved at booking — the seam this commit consumes),
  [0042](0042-index-options-only-scope-ibkr-sole-broker.md) (index-only, IBKR sole broker, SX5E
  live), and [0028](0028-configuration-and-reproducibility-standard.md) (no business/secret literal
  in `.py`; config/credentials from versioned/gitignored sources).

## Context

Booking a position is the one action that *mutates the book*. TARGET §2 #4 requires it to sit
behind a **password** — an explicit human gate — and §6 requires the book to be accounted from an
**append-only audit log** of decisions, not from intentions. The fills-based position store
([landed](../../packages/execution/src/algotrading/execution/ledger.py)) is the read side; this is
the gated write path that feeds it. Two design questions had non-obvious answers worth recording.

## Decision

**1. The gate is `scrypt` + `secrets.compare_digest`, configured from the environment.** The
operator's password is verified against a stored `(salt, digest)` where `digest = scrypt(password,
salt)` with pinned RFC-7914 interactive cost parameters. The comparison is constant-time
(`secrets.compare_digest`). The salt and digest are read from the process environment
(`$HOME/.env`: `BOOKING_GATE_SCRYPT_SALT` / `BOOKING_GATE_SCRYPT_HASH`) — never a `.py` literal, never
the plaintext password, never homegrown crypto (AGENTS.md house rules). A provisioning helper
(`hash_password`) mints the stored digest once in the operator's shell. Verification is
**fail-closed**: a wrong/absent password, an unconfigured gate, or a malformed (non-hex) config each
return a *labelled* `GateBlock`, never an exception for the expected "wrong password" path and never
a default-open.

Rejected: a plaintext or simple-hash (md5/sha-1, unsalted) password — defeats the point; and a
bespoke KDF — the bar is to lean on a proven primitive, not to invent one.

**2. The booking audit log is a *separate* append-only store from the fills ledger.** A commit
appends fills to the `FillsLedger` *and* one `BookingAudit` record to a distinct `BookingAuditLog`;
a **block** appends *only* an audit record (no fill). Keeping them separate is what lets the audit
log be the complete history of *every* decision — including the refusals, which by definition
produce no fill — while the fills ledger stays exactly "the fills the book is summed from". Both
share the same append-only discipline (immutable once written, duplicate id rejected at the door, no
mutate/delete verb, provenance validated at the door, durable JSONL that only grows). The audit
record's provenance stamp is order-independent, so a replay of the decision sequence is
reorder-stable (§6).

Rejected: folding the decision into the fills ledger (a block has no fill to carry it, so refusals
would go unrecorded — the opposite of an audit log); and registering `fills`/`booking_audit` as
infra contract-registry tables (the fills store already chose a JSONL ledger in execution, ADR 0043
lineage — a parallel registry table would be a second, drifting source of the same shape).

**3. Concretization is consumed as an interface, resolved at the BFF boundary.** The commit calls a
`LegResolver` (ADR 0043's pure as-of resolver) it does not own; `booking/concretization_seam.py` is
the commit's interface view of that seam. Until the resolver lands, the BFF injects a pending
placeholder that fails the leg closed (`unresolvable_leg`), so a *verified* booking is honest and
fail-closed, and the endpoint flips to live the moment the real resolver is wired — no commit change.

## Consequences

- The booking commit and the 3B broker-send gate are **two different gates** behind two different
  secrets; the booking module imports no broker and no order-submit symbol (asserted by
  `test_two_gates` + a booking-specific assertion). The booking gate guards a **paper** write only —
  no broker bytes leave the process.
- The book's entire mutation history (commits *and* refusals) is reconstructable from the durable
  `booking_audit.jsonl`, independent of the fills ledger.
- When `execution-fill-concretization` merges, the only wiring is `routers/booking.py::_resolver_for`.
