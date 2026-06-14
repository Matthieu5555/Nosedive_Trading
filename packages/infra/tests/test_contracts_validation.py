"""The frozen contract seam: instrument-key round-trip and write-ahead validation.

These pin the consumer-facing guarantees of `algotrading.infra.contracts`: the
canonical instrument key round-trips, valid records pass, and each class of malformed
record is rejected with an explicit `ContractValidationError` (never a silent coercion),
as TESTING.md requires.
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime

import pytest
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
from fixtures.records import make_record, make_stamp


def _snapshot() -> MarketStateSnapshot:
    # The shared baseline (fixtures.records): one fully valid snapshot; the rejection
    # tests below break exactly one field of it via dataclasses.replace.
    return make_record("market_state_snapshots")


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
    bad_stamp = dataclasses.replace(make_stamp(), stamp_hash="0" * 64)
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


def test_basket_strategy_id_stamp_defaults_none_and_round_trips() -> None:
    # The additive strategy-identity stamp: absent (None) on an operator-authored basket and
    # set on a strategy-emitted one. Both forms must validate and round-trip equal (the
    # additive-nullable contract-evolution path — an existing basket stays valid unchanged).
    unstamped = _basket()
    assert unstamped.strategy_id is None  # additive default keeps existing baskets valid
    assert from_row(Basket, to_row(Basket, unstamped)) == unstamped

    stamped = Basket(
        basket_id="s1-aaa-1m",
        trade_date=date(2026, 6, 5),
        underlying="AAA",
        legs=(BasketLeg("stock", "long", 10.0, "AAA"),),
        strategy_id="S1",
    )
    validate(stamped)
    row = to_row(Basket, stamped)
    assert row["strategy_id"] == "S1"
    assert from_row(Basket, row) == stamped


def test_basket_empty_strategy_id_stamp_is_rejected_with_the_offending_value() -> None:
    # The stamp is optional, but a present-but-blank stamp is malformed (it cannot group a
    # strategy), rejected with the offending value rather than silently treated as absent.
    with pytest.raises(ContractValidationError) as exc:
        Basket(
            basket_id="x",
            trade_date=date(2026, 6, 5),
            underlying="AAA",
            legs=(BasketLeg("stock", "long", 1.0, "AAA"),),
            strategy_id="   ",
        )
    assert exc.value.field == "strategy_id"
    assert exc.value.value == "   "


def test_basket_leg_side_sign_contradiction_is_rejected_with_explicit_error() -> None:
    # Malformed at construction (not a silent normalisation): a "long" leg with a negative
    # quantity is a structured ContractValidationError carrying the offending value.
    with pytest.raises(ContractValidationError) as exc:
        BasketLeg("option", "long", -1.0, "AAA", tenor_label="1m", delta_band="atm")
    assert exc.value.field == "quantity"
    assert exc.value.value == -1.0


def test_basket_leg_surface_side_defaults_to_combined() -> None:
    # An unspecified leg reads off the combined surface (the forward-backing reference, ADR
    # 0048), so adding the field changes no existing caller.
    leg = BasketLeg("option", "long", 1.0, "AAA", tenor_label="1m", delta_band="30dc")
    assert leg.surface_side == "combined"


def test_basket_leg_rejects_unknown_surface_side_with_offending_value() -> None:
    # A wing selection outside {put, call, combined} is a malformed contract, rejected with the
    # offending value — never silently coerced to combined.
    with pytest.raises(ContractValidationError) as exc:
        BasketLeg(
            "option", "long", 1.0, "AAA", tenor_label="1m", delta_band="30dc", surface_side="bid"
        )
    assert exc.value.field == "surface_side"
    assert exc.value.value == "bid"


def test_basket_leg_accepts_put_and_call_wings() -> None:
    for side in ("put", "call", "combined"):
        leg = BasketLeg(
            "option", "long", 1.0, "AAA", tenor_label="1m", delta_band="30dc", surface_side=side
        )
        assert leg.surface_side == side


def test_basket_row_missing_required_column_is_rejected_not_coerced() -> None:
    # Write-ahead read rejection: a stored row missing a required column raises rather than
    # constructing an invalid instance (the additive-nullable rule only forgives Optionals).
    row = to_row(Basket, _basket())
    del row["underlying"]
    with pytest.raises(SchemaCompatibilityError):
        from_row(Basket, row)
