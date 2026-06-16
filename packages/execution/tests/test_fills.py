from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

import pytest
from algotrading.execution import Fill, FillError


def test_a_well_formed_fill_carries_its_lineage_and_signed_quantity(
    make_fill: Callable[..., Fill],
) -> None:
    fill = make_fill(signed_qty=Decimal("-2"), broker_contract_id="conid-777")
    assert fill.booking_id == "bkg-1"
    assert fill.source_basket_id == "bsk-1"
    assert fill.signed_qty == Decimal("-2")
    assert fill.mode == "paper"
    assert fill.broker_contract_id == "conid-777"


@pytest.mark.parametrize(
    "field",
    ["fill_id", "booking_id", "source_basket_id", "underlying", "contract_key"],
)
def test_empty_identity_field_is_a_labelled_rejection(
    make_fill: Callable[..., Fill], field: str
) -> None:
    with pytest.raises(FillError) as exc:
        make_fill(**{field: "   "})
    assert exc.value.field == field


def test_a_zero_quantity_fill_is_not_an_execution(make_fill: Callable[..., Fill]) -> None:
    with pytest.raises(FillError) as exc:
        make_fill(signed_qty=Decimal("0"))
    assert exc.value.field == "signed_qty"


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity")])
def test_a_non_finite_quantity_is_rejected(make_fill: Callable[..., Fill], bad: Decimal) -> None:
    with pytest.raises(FillError) as exc:
        make_fill(signed_qty=bad)
    assert exc.value.field == "signed_qty"


def test_a_float_quantity_is_refused_not_coerced(make_fill: Callable[..., Fill]) -> None:
    with pytest.raises(FillError) as exc:
        make_fill(signed_qty=3.0)  # type: ignore[arg-type]
    assert exc.value.field == "signed_qty"


@pytest.mark.parametrize("bad_price", [0.0, -1.0, float("nan"), float("inf")])
def test_a_non_positive_or_non_finite_price_is_rejected(
    make_fill: Callable[..., Fill], bad_price: float
) -> None:
    with pytest.raises(FillError) as exc:
        make_fill(price=bad_price)
    assert exc.value.field == "price"


def test_a_non_paper_fill_is_rejected_by_construction(make_fill: Callable[..., Fill]) -> None:
    with pytest.raises(FillError) as exc:
        make_fill(mode="live")
    assert exc.value.field == "mode"


def test_a_naive_fill_timestamp_is_rejected(make_fill: Callable[..., Fill]) -> None:
    with pytest.raises(FillError) as exc:
        make_fill(fill_ts=datetime(2026, 6, 12, 15, 30))
    assert exc.value.field == "fill_ts"
