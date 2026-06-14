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

## The seam to the booking commit

The **gated write path** that turns a previewed
[3A ticket](../../tasks/execution-order-ticket.md) into the fills this store ingests is the
split-out [`tasks/execution-booking-commit.md`](../../tasks/execution-booking-commit.md) (this
store is the read side of [`tasks/execution-fills-position-store.md`](../../tasks/execution-fills-position-store.md)).
The boundary between the two is the `Fill` record + the ledger's `append` API: that path
resolves the 3A grid cell to a concrete `contract_key` (the 3B binding), gates on the password,
and appends. This package owns the store and its readers; nothing here gates or transmits.
