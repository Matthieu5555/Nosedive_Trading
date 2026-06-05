"""The committed real Saxo sample replays into deterministic raw events — offline, no broker.

Guards the redistributable demo artifact (``samples/asml_real_2026-06-04.json``, a real
delayed-Saxo ASML slice). The full surface reconstruction (``reconstruct_day`` → SVI) lives in
the analytics pipeline (M2/M4/M7), which is not yet relocated into ``packages/infra``; until it
is, this test guards the part that *is* available end to end: the captured chain decodes back
into the same normalized ``RawMarketEvent`` stream, byte-for-byte, with the Decimal field values
preserved exactly. When the pipeline lands, this is upgraded to assert a converged surface
(see ADR 0021). The oracle is the codec's own round-trip identity, which is legitimate here
because the JSON on disk is the independent fixture, not code output.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from algotrading.infra.storage import events_from_json, events_to_json
from algotrading.infra.universe import instrument_key, parse_instrument_key

_SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "asml_real_2026-06-04.json"


def test_real_sample_decodes_to_saxo_raw_events() -> None:
    events = events_from_json(_SAMPLE.read_text(encoding="utf-8"))
    assert events, "committed sample must not be empty"
    assert all(e.provider == "SAXO" for e in events)
    assert all(e.underlying == "ASML" for e in events)
    # EAV: every event is exactly one observed field of one instrument.
    assert all(e.field_name for e in events)
    # Numeric observations decode to exact Decimals (the __dec__ codec), never lossy floats.
    numeric = [e for e in events if e.field_value is not None]
    assert numeric, "a real chain carries numeric observations"
    assert all(isinstance(e.field_value, Decimal) for e in numeric)


def test_real_sample_replays_deterministically() -> None:
    text = _SAMPLE.read_text(encoding="utf-8")
    once = events_from_json(text)
    # Re-encode then re-decode: the normalized event stream is stable across a round-trip,
    # which is what "replays into the same raw events deterministically" means at this layer.
    twice = events_from_json(events_to_json(once))
    assert twice == once


def test_real_sample_option_keys_round_trip() -> None:
    """Every option instrument key in the real sample is a well-formed canonical key."""
    events = events_from_json(_SAMPLE.read_text(encoding="utf-8"))
    option_keys = {e.instrument_key for e in events if e.instrument_key.startswith("OPT:")}
    assert option_keys, "the chain has option contracts"
    for key in option_keys:
        assert instrument_key(parse_instrument_key(key)) == key
