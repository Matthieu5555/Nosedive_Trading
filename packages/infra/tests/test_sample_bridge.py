"""The broker-raw ↔ contracts sample bridge (ADR 0039) round-trips faithfully.

Guards `universe/sample_bridge.py`: the single converter between the committed broker-raw sample
wire-format (`storage.events.RawMarketEvent`, colon keys, `Decimal`) and the canonical contracts
raw model (`contracts.RawMarketEvent`, pipe keys, `float`). Exercises the real committed IBKR
samples — no broker, runs in the gate. Two invariants:

* **field-level round-trip** — broker-raw → contracts → broker-raw preserves every
  reconstruction-meaningful field (colon key, field name, numeric value, underlying, provider,
  timestamps, broker id), modulo the documented `Decimal`↔`float` boundary (OQ-B).
* **export→reimport stability** — a sample exported from the contracts events and re-imported
  yields byte-identical contracts events (same pipe key, value, and *content-addressed*
  `event_id`), the reproducibility property the regression library depends on.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from algotrading.infra.storage import events_from_json, events_to_json
from algotrading.infra.universe import contracts_to_events, events_to_contracts

_SAMPLES = Path(__file__).resolve().parents[2] / "infra-ibkr" / "samples"
_CASES = [
    ("spy_real_2026-06-04.json", "SPY"),
    ("asml_real_2026-06-05.json", "ASML"),
]
_PROVIDER = "IBKR"


def _numeric(events: list) -> list:
    """The events the bridge carries into the contracts schema (numeric values only)."""
    return [e for e in events if not isinstance(e.field_value, (str, type(None)))]


@pytest.mark.parametrize(("filename", "underlying"), _CASES)
def test_broker_raw_round_trips_through_contracts(filename: str, underlying: str) -> None:
    broker = events_from_json((_SAMPLES / filename).read_text(encoding="utf-8"))
    numeric = _numeric(broker)
    assert numeric, "sample must carry numeric observations"
    trade_date = max(e.receipt_ts for e in broker).date()

    contracts = events_to_contracts(broker, trade_date=trade_date)
    assert len(contracts) == len(numeric)  # categorical/None skipped, order preserved

    back = contracts_to_events(contracts, provider=_PROVIDER)
    assert len(back) == len(numeric)
    for orig, got in zip(numeric, back, strict=True):
        # Colon instrument key survives colon → pipe → colon (canonical keys round-trip).
        assert got.instrument_key == orig.instrument_key
        assert got.field_name == orig.field_name
        assert got.underlying == orig.underlying
        assert got.provider == _PROVIDER
        assert got.receipt_ts == orig.receipt_ts
        assert got.exchange_ts == orig.exchange_ts
        assert got.contract_id_broker == orig.contract_id_broker
        # Decimal == numeric equality holds across the float boundary (OQ-B): the value is exact
        # to the stored precision (e.g. 9.270000 → 9.27), not widened or truncated.
        assert got.field_value == orig.field_value
        assert got.field_value == Decimal(str(float(orig.field_value)))


@pytest.mark.parametrize(("filename", "underlying"), _CASES)
def test_export_reimport_loop_is_stable(filename: str, underlying: str) -> None:
    broker = events_from_json((_SAMPLES / filename).read_text(encoding="utf-8"))
    trade_date = max(e.receipt_ts for e in broker).date()

    contracts1 = events_to_contracts(broker, trade_date=trade_date)
    # contracts → broker-raw → JSON sample → broker-raw → contracts (the export/regen loop).
    sample_json = events_to_json(contracts_to_events(contracts1, provider=_PROVIDER))
    contracts2 = events_to_contracts(events_from_json(sample_json), trade_date=trade_date)

    assert len(contracts2) == len(contracts1)
    for first, second in zip(contracts1, contracts2, strict=True):
        assert first.instrument_key == second.instrument_key
        assert first.field_name == second.field_name
        assert first.value == second.value
        assert first.canonical_ts == second.canonical_ts
        # The content-addressed id is reproduced by the loop — the dedup/replay anchor holds.
        assert first.event_id == second.event_id
