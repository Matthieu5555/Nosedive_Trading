from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from algotrading.infra.contracts import RawMarketEvent
from algotrading.infra_ibkr.collectors.cp_rest_adapter import (
    CpInstrument,
    CpRestMarketDataAdapter,
    subscribe_message,
    unsubscribe_message,
)

from .conftest import FakeCpTransport

_CONID = 265598
_IK = "OPT:SPY:OPT:20260626:C:758:100:SMART:USD"
_INSTRUMENT = CpInstrument(instrument_key=_IK, conid=_CONID, underlying="SPY")
_RECEIPT = datetime(2026, 6, 4, 18, 29, 21, tzinfo=UTC)
_UPDATED_MS = int((datetime(2026, 6, 4, 18, 29, 20, 115000, tzinfo=UTC) - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds() * 1000)


def _snapshot_transport(snapshot_rows: list[dict[str, Any]]) -> FakeCpTransport:
    return FakeCpTransport(get_response=snapshot_rows)


def _adapter(transport: FakeCpTransport) -> CpRestMarketDataAdapter:
    return CpRestMarketDataAdapter(
        transport, [_INSTRUMENT], session_id="ibkr-cp", now_fn=lambda: _RECEIPT,
        _sleep=lambda _seconds: None,
    )


def test_snapshot_normalizes_rows_to_events() -> None:
    transport = _snapshot_transport(
        [{"conid": _CONID, "84": "9.27", "86": "9.31", "_updated": _UPDATED_MS}]
    )
    events = _adapter(transport).snapshot()
    by_field = {e.field_name: e for e in events}
    assert set(by_field) == {"bid", "ask"}
    assert by_field["bid"].value == 9.27 and by_field["ask"].value == 9.31
    assert by_field["bid"].instrument_key == _IK
    assert by_field["bid"].receipt_ts == _RECEIPT
    assert by_field["bid"].exchange_ts == datetime(2026, 6, 4, 18, 29, 20, 115000, tzinfo=UTC)


def test_snapshot_ignores_unknown_conid() -> None:
    transport = _snapshot_transport([{"conid": 999999, "84": "9.27"}])
    assert _adapter(transport).snapshot() == ()


def test_snapshot_warms_up_a_cold_first_response() -> None:

    def _cold_then_warm(_path: str, _params: dict[str, Any]) -> list[dict[str, Any]]:
        if len(transport.get_calls) == 1:
            return [{"conid": _CONID, "server_id": "q0"}]
        return [{"conid": _CONID, "84": "9.27"}]

    transport = FakeCpTransport(get_responder=_cold_then_warm)
    events = _adapter(transport).snapshot()
    assert [e.field_name for e in events] == ["bid"]
    assert events[0].value == 9.27
    assert len(transport.get_paths) == 2


def test_snapshot_is_read_only() -> None:
    transport = _snapshot_transport([{"conid": _CONID, "84": "9.27"}])
    _adapter(transport).snapshot()
    assert transport.get_paths == ["/iserver/marketdata/snapshot"]
    assert transport.post_paths == []
    assert not any("order" in path for path in transport.get_paths + transport.post_paths)


def test_handle_ws_frame_emits_ticks() -> None:
    transport = _snapshot_transport([])
    adapter = _adapter(transport)
    received: list[RawMarketEvent] = []
    adapter.set_tick_callback(received.append)

    frame = json.dumps({"topic": f"smd+{_CONID}", "conid": _CONID, "84": "9.27", "_updated": _UPDATED_MS})
    adapter._handle_frame(frame)

    assert [e.field_name for e in received] == ["bid"]
    assert received[0].value == 9.27


def test_handle_ws_control_frame_emits_nothing() -> None:
    transport = _snapshot_transport([])
    adapter = _adapter(transport)
    received: list[RawMarketEvent] = []
    adapter.set_tick_callback(received.append)
    adapter._handle_frame(json.dumps({"topic": "system", "hb": 1}))
    assert received == []


def test_subscribe_and_unsubscribe_frames() -> None:
    adapter = _adapter(_snapshot_transport([]))
    assert adapter.subscribe_frames() == (subscribe_message(_CONID),)
    assert subscribe_message(_CONID).startswith(f"smd+{_CONID}+")
    assert adapter.unsubscribe_all() == (unsubscribe_message(_CONID),)
