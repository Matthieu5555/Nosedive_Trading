# infra.contracts — the frozen seam

The typed data contracts and the one storage protocol every other workstream imports.
**M0 owns this package; nobody else edits it in place.** A needed change is a
request routed through M0, because every field ripples to the other workstreams.

## What lives here

- **Instrument identity** — `InstrumentKey` (the 9-field composite key from the
  blueprint, Part I), its canonical string form, and the three event timestamps
  (`exchange_ts`, `receipt_ts`, `canonical_ts`).
- **Table contracts** — the frozen dataclasses, one per table family from the
  blueprint data model (Part IV.C / Part IX data dictionary): `InstrumentMaster`,
  `RawMarketEvent`, `DailyBar`, `MarketStateSnapshot`, `ForwardCurvePoint`, `IvPoint`,
  `SurfaceParameters`, `SurfaceGrid`, `PricingResult`, `ProjectedOptionAnalytics`,
  `Position`, `Basket`/`BasketLeg`, `RiskAggregate`, `ScenarioResult`, `ScenarioAttribution`,
  `QcResult`, `TriageRecord`. Each derived
  record carries a `ProvenanceStamp` (from `algotrading.core`) and a `source_snapshot_ts`.
  `DailyBar` is the underlying daily-OHLC price-history product (index + constituents, for
  the candlestick charts) — a **distinct** product from the option `MarketStateSnapshot`,
  provider-partitioned per ADR 0034 §4 (P0.3 / WS 1E); `PricingResult` carries the full
  five-Greek dollar layer (`dollar_delta/gamma/vega/theta/rho`, ADR 0036) with
  `dollar_theta`/`dollar_rho` additive-nullable, plus the second-order set
  (`vanna`/`volga`/`charm` + their `dollar_*`) and `rt_vega`/`dollar_rt_vega` (running-time /
  annualised vega `vega/√T`, ADR 0049) — all raw + cash, additive-nullable.
  `ProjectedOptionAnalytics` is the WS 1F tenor × delta-band grid cell (decimal **and** dollar
  Greeks side by side — including `rt_vega` per strike, each dollar number unit-tagged) —
  provider-partitioned, stored under the `analytics` layer; produced
  by `surfaces.project_grid`. `ScenarioAttribution` is the WS 2C by-Greek decomposition of a
  scenario's stress PnL — the named dollar contributions (`delta_pnl`/`gamma_pnl`/`vega_pnl`/
  `theta_pnl`), their lumped `approx_pnl`, the `full_reprice_pnl` oracle, and the `residual`
  between them, at `level` `position` or `book` (the book record carries the `__book__`
  sentinel in `contract_key`); produced by `risk.attribution`. `Basket`/`BasketLeg` is the WS 2A
  multi-leg position model (an operator INPUT, like `Position` — no provenance stamp): a `Basket`
  is an ordered, named, side-aware set of `BasketLeg`s priced against one `(trade_date,
  underlying[, provider])` analytics snapshot (its `legs` round-trip as one JSON column). A
  `BasketLeg` references one WS-1F grid cell by its coordinate (`underlying`, `tenor_label`,
  `delta_band`) for an option, or the underlying alone for a stock — **not** a canonical
  `contract_key`, because `ProjectedOptionAnalytics` is addressed by that grid coordinate and
  carries no per-contract key; `side`↔`quantity`-sign consistency is enforced at construction.
  The basket is priced by **book-additive summation** of the per-leg dollar Greeks WS-1F already
  produced (`risk.multileg.basket_risk`), never a recompute — see the risk README.
- **Diagnostics bundles** — `ForwardDiagnostics`, `IvDiagnostics`, `SurfaceFitDiagnostics`.
- **Registry + validation** — `spec_for_table` / `table_for_contract` and
  `validate` / `validate_record` (write-ahead validation; rejects, never coerces).
  The positivity/non-negativity/tz-aware rules run on strict pydantic
  `TypeAdapter`s (`Gt`/`Ge`/`AwareDatetime`) mapped back onto
  `ContractValidationError`; the numeric type-identity check (exactly Python
  `int`/`float`, bools and numpy scalars rejected) stays hand-rolled because
  pydantic's strict numbers accept anything implementing the number protocol.
  The write door's exact accept/reject behavior — including its
  numeric-fields-only scope — is pinned by
  `tests/test_contracts_plane_golden.py` (the sanctioned-unfreeze gate for this
  M0 seam, owner-ruled 2026-06-12).
- **The frozen storage protocol** — `StorageRepository` (`ports.py`): the storage seam.
  Table-keyed read/write/list over the contract dataclasses, with the
  versioned-restatement semantics (`version=None` = live; `version=<V>` = one
  restatement; the two never mix; raw append-only tables refuse a versioned write). M1
  implements it; everyone reads and writes through it.
- **The content-addressed event id** — `content_event_id` (`broker.py`): derives a
  deterministic, cross-process id for a tick from `(instrument_key, field_name,
  sequence)`. This is the idempotency primitive for capture; re-capturing the same
  observation yields the same id.

> The old broker-agnostic *pull* seam — `BrokerSession`/`BrokerTick` protocols that
> once lived here — is **retired** (ADRs 0023/0027). The live market-data seam is now
> "normalize every broker into `RawMarketEvent` in the catalog": IBKR via Nautilus's
> adapter (plus our Client-Portal REST transport), Saxo/Deribit via our own adapters,
> all emitting the push `collectors.BrokerTick` onto the one `RawCollector`. Only
> `content_event_id` survives here. So this package now exposes exactly one protocol
> (`StorageRepository`).

## Rules

- Numbers are `float`/`int`, never decimal-strings. Timestamps are timezone-aware.
- The contracts are the *only* objects that cross a layer boundary (see
  `tasks/TESTING.md`). Consumers depend on a protocol, never on a concrete store or
  a broker SDK type.
