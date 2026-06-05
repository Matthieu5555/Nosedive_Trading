"""The committed real IBKR samples replay into deterministic raw events — offline, no Gateway.

Guards the redistributable demo artifacts (``samples/spy_real_2026-06-04.json`` and
``samples/asml_real_2026-06-05.json`` — real delayed-IBKR slices), giving the repo a second
provider's data alongside the Saxo sample. Unlike the adapter/discovery tests, this needs no
``ib_async``: it exercises the captured raw layer, not the live wiring, so it runs in the gate.
The full surface reconstruction (``reconstruct_day`` → multi-maturity SVI) lands when the
analytics pipeline relocates into ``packages/infra`` (ADR 0021); until then this guards the
deterministic raw-event replay that is available end to end.
"""

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
