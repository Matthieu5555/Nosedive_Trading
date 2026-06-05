"""Tests for SaxoUnderlyingProbe — InfoPrices spot poll, mocked transport, no network."""

from __future__ import annotations

from algotrading.infra.universe import Underlying, parse_instrument_key
from algotrading.infra_saxo.collectors.saxo_underlying import SaxoUnderlyingProbe

_REF = {"Data": [{"Identifier": 15629, "Description": "ASML Holding"}]}
_INFO = {"Quote": {"Bid": 760.0, "Ask": 760.5, "Mid": 760.25, "PriceTypeBid": "Delayed"}}


class _FakeTransport:
    """Routes GET by path: /ref/... -> instrument lookup, /trade/... -> InfoPrices snapshot."""

    def __init__(self, ref: dict, info: dict) -> None:
        self._ref = ref
        self._info = info
        self.calls: list[tuple[str, dict]] = []

    def get(self, path: str, params: dict) -> dict:
        self.calls.append((path, params))
        return self._ref if "instruments" in path else self._info


def test_fetch_emits_underlying_ticks() -> None:
    probe = SaxoUnderlyingProbe(_FakeTransport(_REF, _INFO), symbol="ASML", currency="EUR")
    ticks = probe.fetch()
    by_field = {t.field_name: t for t in ticks}
    assert set(by_field) == {"bid", "ask", "last"}
    assert by_field["bid"].value == 760.0
    assert by_field["ask"].value == 760.5
    assert by_field["last"].value == 760.25  # Mid -> last (reference fallback)
    assert all(t.provider == "SAXO" and t.underlying == "ASML" for t in ticks)


def test_fetch_key_parses_as_underlying() -> None:
    probe = SaxoUnderlyingProbe(_FakeTransport(_REF, _INFO), symbol="ASML", currency="EUR")
    tick = probe.fetch()[0]
    instrument = parse_instrument_key(tick.instrument_key)
    assert isinstance(instrument, Underlying)
    assert instrument.symbol == "ASML"
    assert instrument.currency == "EUR"


def test_fetch_no_access_returns_empty() -> None:
    info = {"Quote": {"PriceTypeBid": "NoAccess"}}
    probe = SaxoUnderlyingProbe(_FakeTransport(_REF, info), symbol="ASML", currency="EUR")
    assert probe.fetch() == []


def test_fetch_error_code_returns_empty() -> None:
    info = {"Quote": {"Bid": 1.0, "ErrorCode": "NoAccess"}}
    probe = SaxoUnderlyingProbe(_FakeTransport(_REF, info), symbol="ASML", currency="EUR")
    assert probe.fetch() == []


def test_fetch_unresolved_uic_returns_empty() -> None:
    probe = SaxoUnderlyingProbe(_FakeTransport({"Data": []}, _INFO), symbol="NOPE", currency="EUR")
    assert probe.fetch() == []


def test_uic_resolved_once_and_cached() -> None:
    transport = _FakeTransport(_REF, _INFO)
    probe = SaxoUnderlyingProbe(transport, symbol="ASML", currency="EUR")
    probe.fetch()
    probe.fetch()
    ref_calls = [c for c in transport.calls if "instruments" in c[0]]
    assert len(ref_calls) == 1  # Uic resolved once, then cached


def test_failed_uic_resolution_is_not_retried() -> None:
    transport = _FakeTransport({"Data": []}, _INFO)
    probe = SaxoUnderlyingProbe(transport, symbol="NOPE", currency="EUR")
    assert probe.fetch() == []
    assert probe.fetch() == []
    ref_calls = [c for c in transport.calls if "instruments" in c[0]]
    assert len(ref_calls) == 1  # failure cached: not re-hit every poll
