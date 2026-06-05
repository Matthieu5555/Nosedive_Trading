"""Tests for parse_strike_frame and SaxoMarketDataAdapter — all mocked, no network."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from algotrading.infra.collectors.collector import FeedFault
from algotrading.infra.collectors.normalize import BrokerTick
from algotrading.infra_saxo.collectors.saxo_adapter import (
    SaxoMarketDataAdapter,
    _atm_start_index,
    _iter_expiry_strikes,
    _parse_last_updated,
    parse_stream_frame,
    parse_strike_frame,
)

_CALL_KEY = "OPT:SPY:OPT:20250627:C:530:100:SAXO_11111111:USD"
_PUT_KEY = "OPT:SPY:OPT:20250627:P:530:100:SAXO_11111112:USD"


def _make_frame(ref_id: str, payload: bytes, fmt: int = 0) -> bytes:
    """Build one Saxo binary streaming message: header + ascii ref id + payload."""
    ref = ref_id.encode("ascii")
    return (
        (1).to_bytes(8, "little")  # message id
        + b"\x00\x00"  # reserved
        + bytes([len(ref)])  # ref id length
        + ref
        + bytes([fmt])  # payload format (0 = JSON)
        + len(payload).to_bytes(4, "little")  # payload size
        + payload
    )


_STRIKE_PAYLOAD = {
    "Strikes": [
        {
            "Strike": 530.0,
            "Call": {
                "Bid": 6.30,
                "Ask": 6.50,
                "Greeks": {
                    "Delta": 0.55,
                    "Gamma": 0.04,
                    "Vega": 0.21,
                    "Theta": -0.08,
                    "MidVolatility": 0.185,
                },
            },
            "Put": {
                "Bid": 5.80,
                "Ask": 6.00,
                "Greeks": {
                    "Delta": -0.45,
                    "Gamma": 0.04,
                    "Vega": 0.21,
                    "Theta": -0.07,
                    "MidVolatility": 0.185,
                },
            },
        }
    ]
}


# ---------------------------------------------------------------------------
# parse_strike_frame — pure unit tests
# ---------------------------------------------------------------------------


def test_parse_strike_frame_emits_mark_iv(sample_ws_strike_frame) -> None:
    ticks = parse_strike_frame(sample_ws_strike_frame, call_key=_CALL_KEY, put_key=_PUT_KEY)
    iv_ticks = [t for t in ticks if t.field_name == "mark_iv"]
    # mark_iv emitted for both call and put
    assert len(iv_ticks) == 2
    keys = {t.instrument_key for t in iv_ticks}
    assert keys == {_CALL_KEY, _PUT_KEY}
    # Greeks.MidVolatility is already a decimal vol (not a percent) — emitted as-is.
    assert all(abs(t.value - 0.185) < 1e-9 for t in iv_ticks)


def test_parse_strike_frame_emits_bid_ask(sample_ws_strike_frame) -> None:
    ticks = parse_strike_frame(sample_ws_strike_frame, call_key=_CALL_KEY, put_key=_PUT_KEY)
    call_bid = next(
        (t for t in ticks if t.instrument_key == _CALL_KEY and t.field_name == "bid"), None
    )
    assert call_bid is not None
    assert call_bid.value == pytest.approx(6.30)


def test_parse_strike_frame_emits_greeks(sample_ws_strike_frame) -> None:
    ticks = parse_strike_frame(sample_ws_strike_frame, call_key=_CALL_KEY, put_key=_PUT_KEY)
    field_names = {t.field_name for t in ticks if t.instrument_key == _CALL_KEY}
    assert {"delta", "gamma", "vega", "theta"}.issubset(field_names)


def test_parse_strike_frame_no_mark_iv(sample_ws_strike_frame_no_iv) -> None:
    ticks = parse_strike_frame(sample_ws_strike_frame_no_iv, call_key=_CALL_KEY, put_key=_PUT_KEY)
    iv_ticks = [t for t in ticks if t.field_name == "mark_iv"]
    assert len(iv_ticks) == 0


def test_parse_strike_frame_timestamp_propagated(sample_ws_strike_frame) -> None:
    ts = datetime(2025, 6, 27, 12, 0, 0, tzinfo=UTC)
    ticks = parse_strike_frame(sample_ws_strike_frame, call_key=_CALL_KEY, put_key=_PUT_KEY, ts=ts)
    assert all(t.exchange_ts == ts for t in ticks)


def test_parse_strike_frame_underlying_extracted(sample_ws_strike_frame) -> None:
    ticks = parse_strike_frame(sample_ws_strike_frame, call_key=_CALL_KEY, put_key=_PUT_KEY)
    assert all(t.underlying == "SPY" for t in ticks)


# ---------------------------------------------------------------------------
# SaxoMarketDataAdapter — mocked transport
# ---------------------------------------------------------------------------


def test_set_callbacks_stored() -> None:
    transport = MagicMock()
    adapter = SaxoMarketDataAdapter(transport)
    tick_cb = MagicMock()
    fault_cb = MagicMock()
    adapter.set_tick_callback(tick_cb)
    adapter.set_fault_callback(fault_cb)
    assert adapter._tick_cb is tick_cb
    assert adapter._fault_cb is fault_cb


def test_subscribe_empty_keys_is_noop() -> None:
    transport = MagicMock()
    adapter = SaxoMarketDataAdapter(transport)
    adapter.subscribe([])
    transport.post.assert_not_called()


def test_emit_fault_calls_callback() -> None:
    transport = MagicMock()
    adapter = SaxoMarketDataAdapter(transport)
    faults: list[FeedFault] = []
    adapter.set_fault_callback(faults.append)
    adapter._emit_fault("test error")
    assert len(faults) == 1
    assert faults[0].kind == "other"
    assert "test error" in faults[0].message


def test_emit_fault_no_callback_logs_warning() -> None:
    transport = MagicMock()
    adapter = SaxoMarketDataAdapter(transport)
    # must not raise even without a fault callback
    adapter._emit_fault("silent error")


def test_handle_frame_malformed_json_emits_fault() -> None:
    transport = MagicMock()
    adapter = SaxoMarketDataAdapter(transport)  # default reference_id == "chain1"
    faults: list[FeedFault] = []
    adapter.set_fault_callback(faults.append)
    adapter._handle_frame(_make_frame("chain1", b"not json {{{"))
    assert len(faults) == 1


def test_handle_frame_valid_emits_ticks() -> None:
    import json

    transport = MagicMock()
    adapter = SaxoMarketDataAdapter(transport)
    adapter._subscribed_keys = [_CALL_KEY, _PUT_KEY]
    ticks: list[BrokerTick] = []
    adapter.set_tick_callback(ticks.append)

    adapter._handle_frame(_make_frame("chain1", json.dumps(_STRIKE_PAYLOAD).encode()))
    field_names = {t.field_name for t in ticks}
    assert {"mark_iv", "bid", "ask"}.issubset(field_names)


def test_handle_frame_wrong_reference_id_is_ignored() -> None:
    import json

    transport = MagicMock()
    adapter = SaxoMarketDataAdapter(transport)
    adapter._subscribed_keys = [_CALL_KEY, _PUT_KEY]
    ticks: list[BrokerTick] = []
    adapter.set_tick_callback(ticks.append)

    adapter._handle_frame(_make_frame("other_ref", json.dumps(_STRIKE_PAYLOAD).encode()))
    assert len(ticks) == 0


def test_handle_frame_protobuf_payload_emits_fault() -> None:
    transport = MagicMock()
    adapter = SaxoMarketDataAdapter(transport)
    faults: list[FeedFault] = []
    adapter.set_fault_callback(faults.append)
    adapter._handle_frame(_make_frame("chain1", b"\x08\x01binary", fmt=1))
    assert len(faults) == 1
    assert "protobuf" in faults[0].message.lower()


def test_handle_frame_heartbeat_is_benign() -> None:
    transport = MagicMock()
    adapter = SaxoMarketDataAdapter(transport)
    faults: list[FeedFault] = []
    ticks: list[BrokerTick] = []
    adapter.set_fault_callback(faults.append)
    adapter.set_tick_callback(ticks.append)
    adapter._handle_frame(_make_frame("_heartbeat", b"{}"))
    assert faults == []
    assert ticks == []


# ---------------------------------------------------------------------------
# parse_stream_frame — binary framing
# ---------------------------------------------------------------------------


def test_parse_stream_frame_single_message() -> None:
    raw = _make_frame("chain1", b'{"ok":true}')
    msgs = parse_stream_frame(raw)
    assert len(msgs) == 1
    ref_id, fmt, payload = msgs[0]
    assert ref_id == "chain1"
    assert fmt == 0
    assert payload == b'{"ok":true}'


def test_parse_stream_frame_multiple_messages_in_one_frame() -> None:
    raw = _make_frame("chain1", b'{"a":1}') + _make_frame("_heartbeat", b"{}")
    msgs = parse_stream_frame(raw)
    assert [m[0] for m in msgs] == ["chain1", "_heartbeat"]


def test_parse_stream_frame_truncated_tail_ignored() -> None:
    raw = _make_frame("chain1", b'{"a":1}') + b"\x01\x02\x03"  # partial second message
    msgs = parse_stream_frame(raw)
    assert len(msgs) == 1
    assert msgs[0][0] == "chain1"


def test_parse_stream_frame_preserves_payload_format() -> None:
    raw = _make_frame("chain1", b"\x08\x01", fmt=1)
    assert parse_stream_frame(raw)[0][1] == 1


def test_extract_uic_from_saxo_key() -> None:
    key = "OPT:SPY:OPT:20250627:C:530:100:SAXO_11111111:USD"
    assert SaxoMarketDataAdapter._extract_uic(key) == 11111111


def test_extract_uic_invalid_key_raises() -> None:
    with pytest.raises(ValueError):
        SaxoMarketDataAdapter._extract_uic("OPT:SPY:OPT:20250627:C:530:100:SAXO:USD")


def test_subscribe_uses_configured_asset_type(monkeypatch) -> None:
    transport = MagicMock()
    adapter = SaxoMarketDataAdapter(transport, asset_type="EtfOption")
    # Do not spin up a real WebSocket listener thread in a unit test.
    monkeypatch.setattr(adapter, "_start_ws_listener", lambda: None)
    adapter.subscribe([_CALL_KEY])
    transport.post.assert_called_once()
    path, body = transport.post.call_args.args
    assert path == "/trade/v1/optionschain/subscriptions"
    assert body["Arguments"]["AssetType"] == "EtfOption"


def test_subscribe_defaults_to_stock_option(monkeypatch) -> None:
    transport = MagicMock()
    adapter = SaxoMarketDataAdapter(transport)
    monkeypatch.setattr(adapter, "_start_ws_listener", lambda: None)
    adapter.subscribe([_CALL_KEY])
    assert transport.post.call_args.args[1]["Arguments"]["AssetType"] == "StockOption"


def test_subscribe_single_expiry_by_default(monkeypatch) -> None:
    transport = MagicMock()
    adapter = SaxoMarketDataAdapter(transport)
    monkeypatch.setattr(adapter, "_start_ws_listener", lambda: None)
    adapter.subscribe([_CALL_KEY])
    expiries = transport.post.call_args.args[1]["Arguments"]["Expiries"]
    assert [e["Index"] for e in expiries] == [0]


def test_subscribe_requests_multiple_expiries(monkeypatch) -> None:
    transport = MagicMock()
    adapter = SaxoMarketDataAdapter(transport, n_expiries=3)
    monkeypatch.setattr(adapter, "_start_ws_listener", lambda: None)
    adapter.subscribe([_CALL_KEY])
    expiries = transport.post.call_args.args[1]["Arguments"]["Expiries"]
    assert [e["Index"] for e in expiries] == [0, 1, 2]
    assert all(e["StrikeStartIndex"] == 0 for e in expiries)  # no spot -> lowest strikes


# ---------------------------------------------------------------------------
# P5 — ATM-centred strike windowing (StrikeStartIndex)
# ---------------------------------------------------------------------------


def test_atm_start_index_centres_window_on_spot() -> None:
    strikes = [float(i) for i in range(100)]
    assert _atm_start_index(strikes, spot=50.0, window=20) == 40  # ATM idx 50, window/2=10
    assert _atm_start_index(strikes, spot=-5.0, window=20) == 0  # spot below all -> clamp low
    assert _atm_start_index(strikes, spot=200.0, window=20) == 80  # spot above all -> clamp high
    assert _atm_start_index(strikes, spot=50.0, window=200) == 0  # window covers everything
    assert _atm_start_index([], spot=50.0, window=20) == 0  # empty -> 0


def test_subscribe_centres_strikes_on_reference_spot(monkeypatch) -> None:
    # 60 strikes (200..3150 by 50) on one expiry; with 2 expiry windows the budget is 50 strikes,
    # so the window must START past 0 to centre on a spot up in the chain (else only deep-ITM).
    transport = MagicMock()
    adapter = SaxoMarketDataAdapter(transport, n_expiries=2)
    monkeypatch.setattr(adapter, "_start_ws_listener", lambda: None)
    keys = [
        f"OPT:SPY:OPT:20260717:{r}:{200 + 50 * i}:100:SAXO_99:USD"
        for i in range(60)
        for r in ("C", "P")
    ]
    adapter.set_reference_spot(2800.0)  # ATM near the top of the chain
    adapter.subscribe(keys)
    expiries = transport.post.call_args.args[1]["Arguments"]["Expiries"]
    assert expiries[0]["StrikeStartIndex"] > 0  # centred up-chain, not the deep-ITM low end


# ---------------------------------------------------------------------------
# _iter_expiry_strikes — payload-shape tolerance, expiry grouping
# ---------------------------------------------------------------------------


def test_iter_expiry_strikes_nested_keeps_expiry_index() -> None:
    payload = {
        "Expiries": [
            {"Index": 0, "Strikes": [{"Index": 0}, {"Index": 1}]},
            {"Index": 3, "Strikes": [{"Index": 0}]},
        ]
    }
    groups = _iter_expiry_strikes(payload)
    assert [(e, len(s)) for e, s in groups] == [(0, 2), (3, 1)]


def test_iter_expiry_strikes_flat_defaults_to_expiry_zero() -> None:
    groups = _iter_expiry_strikes({"Strikes": [{"Index": 0}]})
    assert [(e, len(s)) for e, s in groups] == [(0, 1)]


def test_iter_expiry_strikes_unwraps_data_envelope() -> None:
    groups = _iter_expiry_strikes({"Data": {"Expiries": [{"Index": 2, "Strikes": [{"Index": 0}]}]}})
    assert [(e, len(s)) for e, s in groups] == [(2, 1)]


# ---------------------------------------------------------------------------
# _parse_last_updated — delayed exchange timestamp
# ---------------------------------------------------------------------------


def test_parse_last_updated_iso_z() -> None:
    dt = _parse_last_updated({"LastUpdated": "2026-06-04T10:54:54Z"})
    assert dt == datetime(2026, 6, 4, 10, 54, 54, tzinfo=UTC)


def test_parse_last_updated_absent_returns_none() -> None:
    assert _parse_last_updated({}) is None


def test_parse_last_updated_invalid_returns_none() -> None:
    assert _parse_last_updated({"LastUpdated": "not-a-date"}) is None


# ---------------------------------------------------------------------------
# Snapshot -> Index map -> delta resolution (the Stage B fix)
# ---------------------------------------------------------------------------

_SNAPSHOT_NESTED = {
    "LastUpdated": "2026-06-04T10:54:54Z",
    "Expiries": [
        {
            "Index": 0,
            "Strikes": [
                {
                    "Index": 0,
                    "Strike": 530.0,
                    "Call": {"Bid": 6.30, "Ask": 6.50},
                    "Put": {"Bid": 5.80, "Ask": 6.00},
                }
            ],
        }
    ],
}


def _adapter_with_snapshot() -> tuple[SaxoMarketDataAdapter, list[BrokerTick]]:
    """Build an adapter wired to a tick collector and driven once with the nested snapshot.

    Returns the adapter (Index map populated) and the list collecting emitted ticks.
    """
    adapter = SaxoMarketDataAdapter(MagicMock())
    adapter._subscribed_keys = [_CALL_KEY, _PUT_KEY]
    ticks: list[BrokerTick] = []
    adapter.set_tick_callback(ticks.append)
    adapter._handle_payload(_SNAPSHOT_NESTED)
    return adapter, ticks


def test_snapshot_builds_index_map() -> None:
    adapter, _ = _adapter_with_snapshot()
    assert adapter._index_map == {(0, 0): (_CALL_KEY, _PUT_KEY)}


def test_delta_resolved_by_index_emits_tick() -> None:
    adapter, ticks = _adapter_with_snapshot()
    ticks.clear()
    # Delta carries no Strike price — only the positional Index + the changed field.
    delta = {"Expiries": [{"Index": 0, "Strikes": [{"Index": 0, "Call": {"Bid": 6.40}}]}]}
    adapter._handle_payload(delta)
    call_bid = [t for t in ticks if t.instrument_key == _CALL_KEY and t.field_name == "bid"]
    assert len(call_bid) == 1
    assert call_bid[0].value == pytest.approx(6.40)


def test_delta_partial_emits_only_present_fields() -> None:
    adapter, ticks = _adapter_with_snapshot()
    ticks.clear()
    delta = {"Expiries": [{"Index": 0, "Strikes": [{"Index": 0, "Call": {"Bid": 6.40}}]}]}
    adapter._handle_payload(delta)
    assert {t.field_name for t in ticks} == {"bid"}


def test_delta_unmapped_index_logs_debug_and_drops(monkeypatch) -> None:
    import algotrading.infra_saxo.collectors.saxo_adapter as mod

    adapter, ticks = _adapter_with_snapshot()
    ticks.clear()
    # A delta for a strike outside our subscribed window is expected filtering: debug, not warning.
    debug = MagicMock()
    monkeypatch.setattr(mod._log, "debug", debug)
    delta = {"Expiries": [{"Index": 0, "Strikes": [{"Index": 99, "Call": {"Bid": 1.0}}]}]}
    adapter._handle_payload(delta)
    assert ticks == []
    debug.assert_called_once()
    assert "outside the subscribed window" in debug.call_args.args[0] and 99 in debug.call_args.args


def test_delta_unknown_index_warns_once() -> None:
    adapter, _ = _adapter_with_snapshot()
    delta = {"Expiries": [{"Index": 0, "Strikes": [{"Index": 99, "Call": {"Bid": 1.0}}]}]}
    adapter._handle_payload(delta)
    adapter._handle_payload(delta)
    assert adapter._unknown_indices == {(0, 99)}


def test_exchange_ts_from_last_updated() -> None:
    _, ticks = _adapter_with_snapshot()
    assert all(t.exchange_ts == datetime(2026, 6, 4, 10, 54, 54, tzinfo=UTC) for t in ticks)


def test_snapshot_strike_missing_index_warns_and_skips_map(monkeypatch) -> None:
    import algotrading.infra_saxo.collectors.saxo_adapter as mod

    adapter = SaxoMarketDataAdapter(MagicMock())
    adapter._subscribed_keys = [_CALL_KEY, _PUT_KEY]
    ticks: list[BrokerTick] = []
    adapter.set_tick_callback(ticks.append)
    warn = MagicMock()
    monkeypatch.setattr(mod._log, "warning", warn)
    # Snapshot strike with a price but no Index: ticks still emitted, but no routing entry built.
    snapshot = {"Expiries": [{"Index": 0, "Strikes": [{"Strike": 530.0, "Call": {"Bid": 6.30}}]}]}
    adapter._handle_payload(snapshot)
    assert any(t.field_name == "bid" for t in ticks)  # snapshot tick still flows
    assert adapter._index_map == {}  # nothing to route deltas with
    warn.assert_called_once()
    assert "no Index" in warn.call_args.args[0]


def test_payload_with_both_expiries_and_flat_strikes_no_double_emit() -> None:
    # When Expiries is present, the flat top-level Strikes is ignored (Expiries is authoritative).
    adapter = SaxoMarketDataAdapter(MagicMock())
    adapter._subscribed_keys = [_CALL_KEY, _PUT_KEY]
    ticks: list[BrokerTick] = []
    adapter.set_tick_callback(ticks.append)
    strike = {"Index": 0, "Strike": 530.0, "Call": {"Bid": 6.30}, "Put": {"Bid": 5.80}}
    adapter._handle_payload({"Strikes": [strike], "Expiries": [{"Index": 0, "Strikes": [strike]}]})
    bids = [t for t in ticks if t.field_name == "bid"]
    assert len(bids) == 2  # one call bid + one put bid, not doubled


def test_subscribe_warns_on_strike_truncation(monkeypatch) -> None:
    import algotrading.infra_saxo.collectors.saxo_adapter as mod

    transport = MagicMock()
    adapter = SaxoMarketDataAdapter(transport)
    monkeypatch.setattr(adapter, "_start_ws_listener", lambda: None)
    warn = MagicMock()
    monkeypatch.setattr(mod._log, "warning", warn)
    # 101 Call + 101 Put pairs = 202 keys > _MAX_STRIKES_PER_SESSION * 2 (200).
    calls = [f"OPT:SPY:OPT:20250627:C:{i}:100:SAXO_{i}:USD" for i in range(101)]
    puts = [f"OPT:SPY:OPT:20250627:P:{i}:100:SAXO_{1000 + i}:USD" for i in range(101)]
    adapter.subscribe(calls + puts)
    warn.assert_called_once()
    assert "window capped" in warn.call_args.args[0]


def test_subscribe_resets_index_map(monkeypatch) -> None:
    adapter = SaxoMarketDataAdapter(MagicMock())
    adapter._index_map[(0, 0)] = (_CALL_KEY, _PUT_KEY)
    monkeypatch.setattr(adapter, "_start_ws_listener", lambda: None)
    adapter.subscribe([_CALL_KEY])
    assert adapter._index_map == {}
