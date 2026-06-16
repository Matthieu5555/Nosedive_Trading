from __future__ import annotations

from datetime import UTC, datetime

from algotrading.infra.contracts import MarketStateSnapshot, RawMarketEvent
from algotrading.infra.contracts.instrument_key import InstrumentKey
from algotrading.infra.snapshots.builder import SnapshotContext, build_snapshot
from algotrading.infra_ibkr.collectors.cp_rest_normalize import snapshot_to_events
from algotrading.infra_ibkr.collectors.cp_rest_wire import SnapshotRow
from algotrading.infra_ibkr.collectors.market_fields import VOLUME

_CP_VOLUME_TAG = "7762"
_CLOSE = datetime(2026, 6, 15, 20, 0, tzinfo=UTC)
_OPTION_KEY = InstrumentKey(
    underlying_symbol="SX5E",
    security_type="OPT",
    exchange="EUREX",
    currency="EUR",
    multiplier=100.0,
    broker_contract_id="44123456",
    expiry=datetime(2026, 9, 19).date(),
    strike=4800.0,
    option_right="C",
)
_SESSION_ID = "SX5E:2026-06-15"


def test_cp_tag_7762_parses_to_volume_field() -> None:
    row = SnapshotRow.model_validate({
        "conid": 44123456, "84": "12.50", "86": "12.60", "31": "12.55", _CP_VOLUME_TAG: "1234",
    })
    assert row.volume == 1234.0
    assert row.bid == 12.50
    assert row.ask == 12.60


def test_cp_tag_7762_sentinel_becomes_none() -> None:
    row = SnapshotRow.model_validate({"conid": 44123456, _CP_VOLUME_TAG: "-1"})
    assert row.volume is None


def test_cp_tag_7762_absent_is_none() -> None:
    row = SnapshotRow.model_validate({"conid": 44123456, "84": "12.50", "86": "12.60"})
    assert row.volume is None


def test_volume_alone_does_not_count_as_market_value() -> None:
    row = SnapshotRow.model_validate({"conid": 44123456, _CP_VOLUME_TAG: "500"})
    assert not row.has_market_value()

    warm_row = SnapshotRow.model_validate({"conid": 44123456, "84": "12.50"})
    assert warm_row.has_market_value()


def test_snapshot_to_events_emits_volume_event_when_present() -> None:
    row = {
        "conid": 44123456,
        "84": "12.50",
        "86": "12.60",
        "31": "12.55",
        _CP_VOLUME_TAG: "1234",
    }
    events = snapshot_to_events(
        row,
        instrument_key=_OPTION_KEY.canonical(),
        underlying="SX5E",
        session_id=_SESSION_ID,
        sequence=5,
        exchange_ts=_CLOSE,
        receipt_ts=_CLOSE,
    )
    by_field = {e.field_name: e for e in events}
    assert VOLUME in by_field, "snapshot_to_events must emit a 'volume' event for tag 7762"
    assert by_field[VOLUME].value == 1234.0
    assert by_field[VOLUME].field_name == "volume"
    assert by_field[VOLUME].instrument_key == _OPTION_KEY.canonical()


def test_snapshot_to_events_omits_volume_when_absent() -> None:
    row = {"conid": 44123456, "84": "12.50", "86": "12.60", "31": "12.55"}
    events = snapshot_to_events(
        row,
        instrument_key=_OPTION_KEY.canonical(),
        underlying="SX5E",
        session_id=_SESSION_ID,
        sequence=5,
        exchange_ts=_CLOSE,
        receipt_ts=_CLOSE,
    )
    field_names = {e.field_name for e in events}
    assert "volume" not in field_names


def test_snapshot_to_events_omits_volume_for_sentinel() -> None:
    row = {"conid": 44123456, "84": "12.50", "86": "12.60", _CP_VOLUME_TAG: "-1"}
    events = snapshot_to_events(
        row,
        instrument_key=_OPTION_KEY.canonical(),
        underlying="SX5E",
        session_id=_SESSION_ID,
        sequence=5,
        exchange_ts=_CLOSE,
        receipt_ts=_CLOSE,
    )
    assert all(e.field_name != "volume" for e in events)


def _make_event(field_name: str, value: float, *, instrument: InstrumentKey) -> RawMarketEvent:
    from algotrading.infra.contracts import content_event_id
    key = instrument.canonical()
    return RawMarketEvent(
        session_id=_SESSION_ID,
        event_id=content_event_id(key, field_name, 0),
        instrument_key=key,
        exchange_ts=_CLOSE,
        receipt_ts=_CLOSE,
        canonical_ts=_CLOSE,
        field_name=field_name,
        value=value,
        trade_date=_CLOSE.date(),
        underlying=instrument.underlying_symbol,
    )


def _make_context(qc_version: str = "qc-1") -> SnapshotContext:
    from algotrading.core.config import QcThresholdConfig
    return SnapshotContext(
        snapshot_ts=_CLOSE,
        qc=QcThresholdConfig(
            version=qc_version, max_spread_pct=0.5, max_quote_age_seconds=30.0, min_chain_count=1
        ),
        calc_ts=_CLOSE,
        config_hashes={"qc": qc_version},
    )


def test_snapshot_builder_fills_volume_from_raw_events() -> None:
    events = [
        _make_event("bid", 12.50, instrument=_OPTION_KEY),
        _make_event("ask", 12.60, instrument=_OPTION_KEY),
        _make_event("last", 12.55, instrument=_OPTION_KEY),
        _make_event("volume", 750.0, instrument=_OPTION_KEY),
    ]
    snapshot = build_snapshot(_OPTION_KEY, events, context=_make_context())
    assert snapshot.volume == 750.0


def test_snapshot_builder_sets_volume_to_none_when_no_volume_event() -> None:
    events = [
        _make_event("bid", 12.50, instrument=_OPTION_KEY),
        _make_event("ask", 12.60, instrument=_OPTION_KEY),
        _make_event("last", 12.55, instrument=_OPTION_KEY),
    ]
    snapshot = build_snapshot(_OPTION_KEY, events, context=_make_context())
    assert snapshot.volume is None


def test_volume_is_additive_nullable_on_market_state_snapshot() -> None:
    from algotrading.core.provenance import stamp

    prov = stamp(
        calc_ts=_CLOSE,
        code_version="snap-1.0.0",
        config_hashes={"qc": "qc-1"},
        source_records=(),
        source_timestamps=(),
    )
    snap = MarketStateSnapshot(
        snapshot_ts=_CLOSE,
        instrument_key=_OPTION_KEY.canonical(),
        reference_spot=4800.0,
        bid=12.50,
        ask=12.60,
        last=12.55,
        spread_pct=0.008,
        reference_type="mid",
        flags=("open",),
        completeness=1.0,
        trade_date=_CLOSE.date(),
        underlying="SX5E",
        provenance=prov,
    )
    assert snap.volume is None

    snap_with_vol = MarketStateSnapshot(
        snapshot_ts=_CLOSE,
        instrument_key=_OPTION_KEY.canonical(),
        reference_spot=4800.0,
        bid=12.50,
        ask=12.60,
        last=12.55,
        spread_pct=0.008,
        reference_type="mid",
        flags=("open",),
        completeness=1.0,
        trade_date=_CLOSE.date(),
        underlying="SX5E",
        provenance=prov,
        volume=750.0,
    )
    assert snap_with_vol.volume == 750.0
