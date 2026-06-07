"""The frozen contract seam: instrument-key round-trip and write-ahead validation.

These pin the consumer-facing guarantees of `algotrading.infra.contracts`: the
canonical instrument key round-trips, valid records pass, and each class of malformed
record is rejected with an explicit `ContractValidationError` (never a silent coercion),
as TESTING.md requires.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime

import pytest
from algotrading.core import source_ref, stamp
from algotrading.infra.contracts import (
    Basket,
    BasketLeg,
    ContractValidationError,
    InstrumentKey,
    MarketStateSnapshot,
    UnknownTableError,
    broker_contract_id_from_canonical,
    spec_for_table,
    table_for_contract,
    validate,
)
from algotrading.infra.storage.errors import SchemaCompatibilityError
from algotrading.infra.storage.serialization import from_row, to_row

_TS = datetime(2026, 6, 5, 14, 30, tzinfo=UTC)


def _stamp():
    return stamp(
        calc_ts=_TS,
        code_version="algotrading-infra-0.1.0",
        config_hashes={"cfg": "cfg"},
        source_records=(source_ref("raw_market_events", "s", "e"),),
        source_timestamps=(_TS,),
    )


def _snapshot() -> MarketStateSnapshot:
    return MarketStateSnapshot(
        snapshot_ts=_TS,
        instrument_key="SPX|IND|CBOE|USD|1|abc||",
        reference_spot=5000.0,
        bid=4999.0,
        ask=5001.0,
        last=5000.0,
        spread_pct=0.0004,
        reference_type="mid",
        flags=(),
        completeness=1.0,
        trade_date=date(2026, 6, 5),
        underlying="SPX",
        provenance=_stamp(),
    )


def test_option_instrument_key_canonical_round_trips() -> None:
    key = InstrumentKey(
        underlying_symbol="SPX",
        security_type="OPT",
        exchange="CBOE",
        currency="USD",
        multiplier=100.0,
        broker_contract_id="con-12345",
        expiry=date(2026, 12, 18),
        strike=5000.0,
        option_right="C",
    )
    canonical = key.canonical()
    assert key.is_option()
    assert broker_contract_id_from_canonical(canonical) == "con-12345"


def test_underlying_and_option_keys_do_not_collide() -> None:
    underlying = InstrumentKey("SPX", "IND", "CBOE", "USD", 1.0, "con-1")
    option = InstrumentKey(
        "SPX", "OPT", "CBOE", "USD", 100.0, "con-1", date(2026, 12, 18), 5000.0, "C"
    )
    assert underlying.canonical() != option.canonical()
    assert not underlying.is_option()


def test_broker_contract_id_from_non_canonical_string_raises() -> None:
    with pytest.raises(ValueError):
        broker_contract_id_from_canonical("not-a-key")


def test_valid_snapshot_passes_validation() -> None:
    validate(_snapshot())  # no raise == pass


def test_registry_maps_contract_to_table() -> None:
    assert table_for_contract(MarketStateSnapshot) == "market_state_snapshots"
    assert spec_for_table("market_state_snapshots").requires_provenance is True
    with pytest.raises(UnknownTableError):
        spec_for_table("no_such_table")


def test_non_finite_numeric_is_rejected() -> None:
    bad = dataclasses.replace(_snapshot(), reference_spot=float("nan"))
    with pytest.raises(ContractValidationError) as exc:
        validate(bad)
    assert exc.value.field == "reference_spot"


def test_naive_datetime_is_rejected() -> None:
    bad = dataclasses.replace(_snapshot(), snapshot_ts=datetime(2026, 6, 5, 14, 30))
    with pytest.raises(ContractValidationError):
        validate(bad)


def test_non_positive_where_positive_required_is_rejected() -> None:
    # reference_spot is a positive_field for market_state_snapshots.
    bad = dataclasses.replace(_snapshot(), reference_spot=0.0)
    with pytest.raises(ContractValidationError):
        validate(bad)


def test_invalid_provenance_surfaces_as_contract_error() -> None:
    bad_stamp = dataclasses.replace(_stamp(), stamp_hash="0" * 64)
    bad = dataclasses.replace(_snapshot(), provenance=bad_stamp)
    with pytest.raises(ContractValidationError) as exc:
        validate(bad)
    assert exc.value.field == "provenance"


def _basket() -> Basket:
    # An option leg (references a grid cell) + a stock leg (spot exposure). The nested
    # ``legs`` tuple is what exercises the JSON-column round-trip.
    return Basket(
        basket_id="rr-aaa-1m",
        trade_date=date(2026, 6, 5),
        underlying="AAA",
        legs=(
            BasketLeg("option", "long", 1.0, "AAA", tenor_label="1m", delta_band="30dc"),
            BasketLeg("option", "short", -1.0, "AAA", tenor_label="1m", delta_band="30dp"),
            BasketLeg("stock", "long", 10.0, "AAA"),
        ),
        provider="ibkr",
    )


def test_basket_contract_round_trips() -> None:
    # C→A seam: a Basket with nested legs serializes to a flat row (legs become one JSON
    # column) and reads back equal, and the live instance validates against the registry.
    basket = _basket()
    validate(basket)  # no raise == passes the registry schema
    assert table_for_contract(Basket) == "baskets"
    row = to_row(Basket, basket)
    assert isinstance(row["legs"], str)  # nested tuple stored as a single JSON column
    assert from_row(Basket, row) == basket


def test_basket_leg_side_sign_contradiction_is_rejected_with_explicit_error() -> None:
    # Malformed at construction (not a silent normalisation): a "long" leg with a negative
    # quantity is a structured ContractValidationError carrying the offending value.
    with pytest.raises(ContractValidationError) as exc:
        BasketLeg("option", "long", -1.0, "AAA", tenor_label="1m", delta_band="atm")
    assert exc.value.field == "quantity"
    assert exc.value.value == -1.0


def test_basket_row_missing_required_column_is_rejected_not_coerced() -> None:
    # Write-ahead read rejection: a stored row missing a required column raises rather than
    # constructing an invalid instance (the additive-nullable rule only forgives Optionals).
    row = to_row(Basket, _basket())
    del row["underlying"]
    with pytest.raises(SchemaCompatibilityError):
        from_row(Basket, row)
