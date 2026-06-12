"""Client Portal market-data adapter (ADR 0024) — REST snapshot + WS frame → RawMarketEvent.

No live socket: a fake transport drives :meth:`snapshot`, and :meth:`_handle_frame` is fed a WS
frame directly. The read-only invariant (ADR 0024 §4) is asserted — the adapter touches only
market-data paths, never an order endpoint.
"""

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

_CONID = 265598
_IK = "OPT:SPY:OPT:20260626:C:758:100:SMART:USD"
_INSTRUMENT = CpInstrument(instrument_key=_IK, conid=_CONID, underlying="SPY")
_RECEIPT = datetime(2026, 6, 4, 18, 29, 21, tzinfo=UTC)
# 2026-06-04T18:29:20.115Z as CP's `_updated` epoch-ms.
_UPDATED_MS = int((datetime(2026, 6, 4, 18, 29, 20, 115000, tzinfo=UTC) - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds() * 1000)


class _FakeTransport:
    def __init__(self, snapshot_rows: list[dict[str, Any]]) -> None:
        self.get_paths: list[str] = []
        self.post_paths: list[str] = []
        self._snapshot_rows = snapshot_rows

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.get_paths.append(path)
        return self._snapshot_rows


def _adapter(transport: _FakeTransport) -> CpRestMarketDataAdapter:
    # _sleep injected as a no-op: a transport whose rows never warm must not stall the test on
    # the snapshot engine's real warm-up sleeps.
    return CpRestMarketDataAdapter(
        transport, [_INSTRUMENT], session_id="ibkr-cp", now_fn=lambda: _RECEIPT,
        _sleep=lambda _seconds: None,
    )


def test_snapshot_normalizes_rows_to_events() -> None:
    transport = _FakeTransport(
        [{"conid": _CONID, "84": "9.27", "86": "9.31", "_updated": _UPDATED_MS}]
    )
    events = _adapter(transport).snapshot()
    by_field = {e.field_name: e for e in events}
    assert set(by_field) == {"bid", "ask"}
    assert by_field["bid"].value == 9.27 and by_field["ask"].value == 9.31
    assert by_field["bid"].instrument_key == _IK
    assert by_field["bid"].receipt_ts == _RECEIPT
    # exchange_ts came from the `_updated` field, not the receipt clock.
    assert by_field["bid"].exchange_ts == datetime(2026, 6, 4, 18, 29, 20, 115000, tzinfo=UTC)


def test_snapshot_ignores_unknown_conid() -> None:
    transport = _FakeTransport([{"conid": 999999, "84": "9.27"}])
    assert _adapter(transport).snapshot() == ()


def test_snapshot_warms_up_a_cold_first_response() -> None:
    """A metadata-only first snapshot (the cold-subscription quirk) is polled until marks appear.

    The adapter rides the shared snapshot engine, so it inherits the warm-up the close capture
    proved live: the first response carries no value tag, the second carries the quote — the
    adapter must emit the warmed quote, not an empty tuple.
    """

    class _ColdThenWarm(_FakeTransport):
        def __init__(self) -> None:
            super().__init__([])
            self._calls = 0

        def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
            self.get_paths.append(path)
            self._calls += 1
            if self._calls == 1:
                return [{"conid": _CONID, "server_id": "q0"}]  # cold: metadata only
            return [{"conid": _CONID, "84": "9.27"}]

    transport = _ColdThenWarm()
    events = _adapter(transport).snapshot()
    assert [e.field_name for e in events] == ["bid"]
    assert events[0].value == 9.27
    assert len(transport.get_paths) == 2  # one cold call, one warm retry — then it stopped


def test_snapshot_is_read_only() -> None:
    transport = _FakeTransport([{"conid": _CONID, "84": "9.27"}])
    _adapter(transport).snapshot()
    # The only endpoint touched is the market-data snapshot; nothing order-related, ever.
    assert transport.get_paths == ["/iserver/marketdata/snapshot"]
    assert transport.post_paths == []
    assert not any("order" in path for path in transport.get_paths + transport.post_paths)


def test_handle_ws_frame_emits_ticks() -> None:
    transport = _FakeTransport([])
    adapter = _adapter(transport)
    received: list[RawMarketEvent] = []
    adapter.set_tick_callback(received.append)

    frame = json.dumps({"topic": f"smd+{_CONID}", "conid": _CONID, "84": "9.27", "_updated": _UPDATED_MS})
    adapter._handle_frame(frame)

    assert [e.field_name for e in received] == ["bid"]
    assert received[0].value == 9.27


def test_handle_ws_control_frame_emits_nothing() -> None:
    transport = _FakeTransport([])
    adapter = _adapter(transport)
    received: list[RawMarketEvent] = []
    adapter.set_tick_callback(received.append)
    adapter._handle_frame(json.dumps({"topic": "system", "hb": 1}))  # heartbeat, not market data
    assert received == []


def test_subscribe_and_unsubscribe_frames() -> None:
    adapter = _adapter(_FakeTransport([]))
    assert adapter.subscribe_frames() == (subscribe_message(_CONID),)
    assert subscribe_message(_CONID).startswith(f"smd+{_CONID}+")
    assert adapter.unsubscribe_all() == (unsubscribe_message(_CONID),)
