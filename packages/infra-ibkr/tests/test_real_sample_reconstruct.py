from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from algotrading.infra.storage import events_from_json, events_to_json
from algotrading.infra.universe import instrument_key, parse_instrument_key

_SAMPLES = Path(__file__).resolve().parents[1] / "samples"
_CASES = [
    ("spy_real_2026-06-04.json", "SPY"),
    ("asml_real_2026-06-05.json", "ASML"),
]


@pytest.mark.parametrize(("filename", "underlying"), _CASES)
def test_real_sample_decodes_to_ibkr_raw_events(filename: str, underlying: str) -> None:
    events = events_from_json((_SAMPLES / filename).read_text(encoding="utf-8"))
    assert events, "committed sample must not be empty"
    assert all(e.provider == "IBKR" for e in events)
    assert all(e.underlying == underlying for e in events)
    numeric = [e for e in events if e.field_value is not None]
    assert numeric, "a real chain carries numeric observations"
    assert all(isinstance(e.field_value, Decimal) for e in numeric)


@pytest.mark.parametrize(("filename", "underlying"), _CASES)
def test_real_sample_replays_deterministically(filename: str, underlying: str) -> None:
    text = (_SAMPLES / filename).read_text(encoding="utf-8")
    once = events_from_json(text)
    twice = events_from_json(events_to_json(once))
    assert twice == once


@pytest.mark.parametrize(("filename", "underlying"), _CASES)
def test_real_sample_option_keys_round_trip(filename: str, underlying: str) -> None:
    events = events_from_json((_SAMPLES / filename).read_text(encoding="utf-8"))
    option_keys = {e.instrument_key for e in events if e.instrument_key.startswith("OPT:")}
    assert option_keys, "the chain has option contracts"
    for key in option_keys:
        assert instrument_key(parse_instrument_key(key)) == key
