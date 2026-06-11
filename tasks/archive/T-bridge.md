# T-bridge — Raw-schema bridge + reproducible sample regeneration

> **QUEUED — claimable. [ADR 0039](../.agent/decisions/0039-raw-schema-bridge-and-sample-regeneration.md)
> accepted (OQ-A synthesis-on-export, OQ-B Decimal→float diagnostic).** Closes the broker-raw ↔
> contracts bridge deferred under [ADR 0021](../.agent/decisions/0021-analytics-core-merge.md). Low
> collision: a new module + `export_sample.py` + a notebook edit; touches no in-flight shared file.

- **Owns:** `packages/infra/src/algotrading/infra/universe/sample_bridge.py` (**new**);
  `scripts/export_sample.py` (rewrite to write); `notebooks/demo_pipeline_ibkr.ipynb` (replace
  the inline conversion in `replay_sample` with the bridge); `packages/infra/tests/test_sample_bridge.py`
  (**new**); `packages/infra/src/algotrading/infra/universe/__init__.py` (export the two functions).
- **Depends on:** ADR 0039 accepted. Independent of T-raw-invariant and of QA-FIX (disjoint files).
- **Blocks:** a committable **SX5E** sample (`packages/infra-ibkr/samples/sx5e_real_<date>.json`)
  — that file additionally needs SX5E raw landed (T-raw-invariant / a gateway re-capture), but the
  *serialization* path is unblocked here. Removes the duplicated conversion logic from the notebook.
- **State going in:** two `RawMarketEvent` schemas with no converter — broker-raw
  (`storage/events.py:33`, colon keys, `Decimal`, `provider`/`contract_id_broker`) vs contracts
  (`contracts/tables.py:43`, pipe keys, `float`, `canonical_ts`). `export_sample.py:85-91` refuses
  to write (`return 2`). The notebook re-implements the conversion inline. Samples are hand-made
  fixtures (commit `3a21d9f`), no generator.

## Objective

One tested bridge module is the single place the two schemas convert, so committed samples become
**reproducible from a stored raw day** (blueprint Part XVI regression library) and the notebook's
hand-rolled copy is deleted.

## What to do (ordered)

1. **`universe/sample_bridge.py` (new).** `events_to_contracts(broker_events, *, trade_date)` and
   `contracts_to_events(contract_events, *, provider)`. Single home for: colon↔pipe key relabel
   (`universe.parse_instrument_key` ↔ `InstrumentKey.canonical`), `Decimal↔float`,
   `collector_session_id↔session_id`, `canonical_ts = exchange_ts or receipt_ts`, and per-
   `(instrument, field)` **sequence re-derivation** so `event_id = content_event_id(key, field, seq)`
   matches the live id. Pin the field-mapping table in the module docstring. Resolve OQ-A
   (provider as arg; `contract_id_broker` from masters when present, else null) per the ADR ruling.
2. **`test_sample_bridge.py` (new).** Round-trip on the committed samples:
   `events_to_contracts(events_from_json(sample))` → write to a temp `ParquetStore` → read back →
   `contracts_to_events(..., provider=...)` → `events_to_json` → assert the curated last-tick set
   is identity (modulo the documented Decimal→float boundary, OQ-B). Independently-derived
   expected values; no golden-only assertions.
3. **`export_sample.py` (rewrite).** Keep the curated last-tick computation; replace `return 2`
   with `contracts_to_events(curated, provider=...)` → `events_to_json` → `Path(out).write_text(...)`.
   `--symbol SX5E --date <d> --out packages/infra-ibkr/samples/sx5e_real_<d>.json` now produces a
   committable sample. Keep `reconstruct_sample.py` as the determinism guard.
4. **Notebook.** Delete the inline conversion loop in `replay_sample`; call
   `events_to_contracts(...)`. The ASML/SPY replays must produce byte-identical outputs to today
   (the bridge encodes the same mapping the notebook did).

## Done when

Root gate green; `test_sample_bridge.py` pins the round-trip; `export_sample.py` writes a valid
sample that `reconstruct_sample.py` replays deterministically; the notebook replays ASML with
identical surface output via the bridge; ADR 0021's bridge deferral is marked closed by ADR 0039.
