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

## The seam to the booking commit

The **gated write path** that turns a previewed
[3A ticket](../../tasks/execution-order-ticket.md) into the fills this store ingests is the
split-out [`tasks/execution-booking-commit.md`](../../tasks/execution-booking-commit.md) (this
store is the read side of [`tasks/execution-fills-position-store.md`](../../tasks/execution-fills-position-store.md)).
The boundary between the two is the `Fill` record + the ledger's `append` API: that path calls
`concretize` to resolve the 3A grid cell to a concrete `contract_key` + paper mark (ADR 0043),
gates on the password, and appends. This package owns the store, the resolver, and its readers;
nothing here gates or transmits.
