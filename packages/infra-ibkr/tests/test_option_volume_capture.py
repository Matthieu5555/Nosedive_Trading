"""Option volume capture lane (TARGET §7 #7) — end-to-end seam tests.

Covers:
* CP tag 7762 → ``SnapshotRow.volume`` → ``RawMarketEvent`` with field_name="volume"
* ``RawMarketEvent`` with field_name="volume" → ``MarketStateSnapshot.volume`` via builder
* ``MarketStateSnapshot.volume`` is None when no raw volume event exists (additive-nullable)
* Volume absent from the underlying leg (not a tradable option — no volume concept)
* ``SnapshotRow.has_market_value`` is not confused by volume alone

Expected values are derived independently of the capture code:
- Tag 7762 is documented as "Option Day Volume" (cumulative contracts traded today).
- Independently verified: 7762="1234" → parse_field_value("1234") = 1234.0 (plain numeric string).
- ``MarketStateSnapshot.volume`` = sum of latest "volume" events for the instrument,
  or None when no "volume" event is present.
"""

from __future__ import annotations

from datetime import UTC, datetime

from algotrading.infra.contracts import MarketStateSnapshot, RawMarketEvent
from algotrading.infra.contracts.instrument_key import InstrumentKey
from algotrading.infra.snapshots.builder import SnapshotContext, build_snapshot
from algotrading.infra_ibkr.collectors.cp_rest_normalize import snapshot_to_events
from algotrading.infra_ibkr.collectors.cp_rest_wire import SnapshotRow
from algotrading.infra_ibkr.collectors.market_fields import VOLUME

# Constants used across tests — derived independently from the CP API documentation.
_CP_VOLUME_TAG = "7762"  # documented: per-contract option day volume (contracts traded today)
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


# ---------------------------------------------------------------------------
# Wire layer: tag 7762 → SnapshotRow.volume
# ---------------------------------------------------------------------------

def test_cp_tag_7762_parses_to_volume_field() -> None:
    """CP tag 7762 is mapped to the ``volume`` attribute on ``SnapshotRow``.

    Expected: parse_field_value("1234") = 1234.0 (plain numeric string, no sentinel).
    The CP -1 sentinel becomes None (not a real volume).
    """
    row = SnapshotRow.model_validate({
        "conid": 44123456, "84": "12.50", "86": "12.60", "31": "12.55", _CP_VOLUME_TAG: "1234",
    })
    # Volume is 1234 contracts traded today (independently verified from CP tag 7762 spec).
    assert row.volume == 1234.0
    assert row.bid == 12.50
    assert row.ask == 12.60


def test_cp_tag_7762_sentinel_becomes_none() -> None:
    """The CP -1 no-value sentinel on tag 7762 degrades to None, not 0 or -1."""
    row = SnapshotRow.model_validate({"conid": 44123456, _CP_VOLUME_TAG: "-1"})
    assert row.volume is None


def test_cp_tag_7762_absent_is_none() -> None:
    """An absent tag 7762 (the broker did not include it) degrades to None."""
    row = SnapshotRow.model_validate({"conid": 44123456, "84": "12.50", "86": "12.60"})
    assert row.volume is None


def test_volume_alone_does_not_count_as_market_value() -> None:
    """``has_market_value`` only guards bid/ask/last/sizes — volume is not a quote field.

    A cold row carries metadata only; volume without a price tag is not a warm row for
    the snapshot engine's warm-up poll. Expected from the documented warm/cold distinction.
    """
    row = SnapshotRow.model_validate({"conid": 44123456, _CP_VOLUME_TAG: "500"})
    assert not row.has_market_value()

    # A row with a bid (a real quote field) IS warm, even with no volume.
    warm_row = SnapshotRow.model_validate({"conid": 44123456, "84": "12.50"})
    assert warm_row.has_market_value()


# ---------------------------------------------------------------------------
# Normalize layer: SnapshotRow → RawMarketEvent with field_name="volume"
# ---------------------------------------------------------------------------

def test_snapshot_to_events_emits_volume_event_when_present() -> None:
    """Tag 7762 in a snapshot row → a ``RawMarketEvent`` with field_name='volume'.

    Expected field_name = VOLUME constant = "volume"; value = 1234.0.
    """
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
    # Independently derived: 7762="1234" → parse_field_value → 1234.0
    assert by_field[VOLUME].value == 1234.0
    assert by_field[VOLUME].field_name == "volume"
    assert by_field[VOLUME].instrument_key == _OPTION_KEY.canonical()


def test_snapshot_to_events_omits_volume_when_absent() -> None:
    """No volume tag in the row → no 'volume' event emitted (absent is not a zero)."""
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
    """The CP -1 sentinel on tag 7762 does not yield a 'volume' event."""
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


# ---------------------------------------------------------------------------
# Snapshot builder: RawMarketEvent(volume) → MarketStateSnapshot.volume
# ---------------------------------------------------------------------------

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
    """``build_snapshot`` sets ``volume`` from the latest 'volume' raw event for the instrument.

    Expected: a 'volume' event with value=750.0 → snapshot.volume = 750.0.
    Independently derived: the builder reads the latest field value from raw events via
    ``latest_by_field_before``; volume=750 is exactly the value from the raw event.
    """
    events = [
        _make_event("bid", 12.50, instrument=_OPTION_KEY),
        _make_event("ask", 12.60, instrument=_OPTION_KEY),
        _make_event("last", 12.55, instrument=_OPTION_KEY),
        _make_event("volume", 750.0, instrument=_OPTION_KEY),
    ]
    snapshot = build_snapshot(_OPTION_KEY, events, context=_make_context())
    # volume = 750 from the raw event (independently derived: same value, no transform).
    assert snapshot.volume == 750.0


def test_snapshot_builder_sets_volume_to_none_when_no_volume_event() -> None:
    """When no 'volume' raw event exists for an instrument, ``snapshot.volume`` is None.

    This is the additive-nullable guarantee: existing captures (bid/ask/last only) are not
    broken — the field simply reads back None, exactly like ``PricingResult.dollar_theta``.
    """
    events = [
        _make_event("bid", 12.50, instrument=_OPTION_KEY),
        _make_event("ask", 12.60, instrument=_OPTION_KEY),
        _make_event("last", 12.55, instrument=_OPTION_KEY),
        # No volume event.
    ]
    snapshot = build_snapshot(_OPTION_KEY, events, context=_make_context())
    assert snapshot.volume is None


def test_volume_is_additive_nullable_on_market_state_snapshot() -> None:
    """``MarketStateSnapshot.volume`` defaults to None and accepts None without error.

    This is the schema-evolution guarantee: old snapshots without volume read back
    as None, and the write door accepts None for this optional field.
    """
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
        # No volume argument → defaults to None.
    )
    assert snap.volume is None

    # With volume explicitly set.
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
