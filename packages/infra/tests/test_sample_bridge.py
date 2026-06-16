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
    return [e for e in events if not isinstance(e.field_value, (str, type(None)))]


@pytest.mark.parametrize(("filename", "underlying"), _CASES)
def test_broker_raw_round_trips_through_contracts(filename: str, underlying: str) -> None:
    broker = events_from_json((_SAMPLES / filename).read_text(encoding="utf-8"))
    numeric = _numeric(broker)
    assert numeric, "sample must carry numeric observations"
    trade_date = max(e.receipt_ts for e in broker).date()

    contracts = events_to_contracts(broker, trade_date=trade_date)
    assert len(contracts) == len(numeric)

    back = contracts_to_events(contracts, provider=_PROVIDER)
    assert len(back) == len(numeric)
    for orig, got in zip(numeric, back, strict=True):
        assert got.instrument_key == orig.instrument_key
        assert got.field_name == orig.field_name
        assert got.underlying == orig.underlying
        assert got.provider == _PROVIDER
        assert got.receipt_ts == orig.receipt_ts
        assert got.exchange_ts == orig.exchange_ts
        assert got.contract_id_broker == orig.contract_id_broker
        assert got.field_value == orig.field_value
        assert got.field_value == Decimal(str(float(orig.field_value)))


@pytest.mark.parametrize(("filename", "underlying"), _CASES)
def test_export_reimport_loop_is_stable(filename: str, underlying: str) -> None:
    broker = events_from_json((_SAMPLES / filename).read_text(encoding="utf-8"))
    trade_date = max(e.receipt_ts for e in broker).date()

    contracts1 = events_to_contracts(broker, trade_date=trade_date)
    sample_json = events_to_json(contracts_to_events(contracts1, provider=_PROVIDER))
    contracts2 = events_to_contracts(events_from_json(sample_json), trade_date=trade_date)

    assert len(contracts2) == len(contracts1)
    for first, second in zip(contracts1, contracts2, strict=True):
        assert first.instrument_key == second.instrument_key
        assert first.field_name == second.field_name
        assert first.value == second.value
        assert first.canonical_ts == second.canonical_ts
        assert first.event_id == second.event_id
