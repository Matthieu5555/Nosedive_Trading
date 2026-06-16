from __future__ import annotations

from datetime import date

import pytest
from algotrading.frontend.demo_book import build_book

TRADE_DATE = date(2026, 6, 16)


def test_builds_three_legs_for_a_rich_name() -> None:
    # A name with an ATM cell, a put wing and a call wing -> three legs.
    cells = {("1m", "atm"), ("1m", "10dp"), ("1m", "10dc")}
    book = build_book({"SX5E": cells}, TRADE_DATE)
    assert len(book.legs) == 3
    bands = {leg.delta_band for leg in book.legs}
    assert bands == {"atm", "10dp", "10dc"}


def test_atm_and_call_are_short_on_first_name_put_is_long() -> None:
    # idx 0 -> sells_vol: ATM and call wing short (qty < 0), put wing long (> 0).
    book = build_book({"SX5E": {("1m", "atm"), ("1m", "10dp"), ("1m", "10dc")}}, TRADE_DATE)
    by_band = {leg.delta_band: leg for leg in book.legs}
    assert by_band["atm"].side == "short" and by_band["atm"].quantity < 0
    assert by_band["10dc"].side == "short" and by_band["10dc"].quantity < 0
    assert by_band["10dp"].side == "long" and by_band["10dp"].quantity > 0


def test_second_name_buys_vol() -> None:
    # Two names sorted: AAA (idx 0, sells vol), BBB (idx 1, buys vol).
    book = build_book(
        {
            "AAA": {("1m", "atm")},
            "BBB": {("1m", "atm")},
        },
        TRADE_DATE,
    )
    by_und = {leg.underlying: leg for leg in book.legs}
    assert by_und["AAA"].side == "short"
    assert by_und["BBB"].side == "long"


def test_tenor_preference_picks_1m_over_12m() -> None:
    cells = {("12m", "atm"), ("1m", "atm"), ("6m", "atm")}
    book = build_book({"SX5E": cells}, TRADE_DATE)
    assert book.legs[0].tenor_label == "1m"


def test_quantities_fan_out_by_name_index() -> None:
    # scale = 10 + 5*idx on the ATM leg -> name 0 gets 10, name 1 gets 15.
    book = build_book({"AAA": {("1m", "atm")}, "BBB": {("1m", "atm")}}, TRADE_DATE)
    by_und = {leg.underlying: abs(leg.quantity) for leg in book.legs}
    assert by_und["AAA"] == pytest.approx(10.0)
    assert by_und["BBB"] == pytest.approx(15.0)


def test_thin_name_contributes_fewer_legs() -> None:
    # Only a put wing available -> exactly one (long) leg, no ATM/call.
    book = build_book({"SAF": {("3m", "10dp")}}, TRADE_DATE)
    assert len(book.legs) == 1
    assert book.legs[0].delta_band == "10dp"
    assert book.legs[0].side == "long"


def test_empty_cells_raises() -> None:
    with pytest.raises(ValueError, match="no resolvable cells"):
        build_book({"SX5E": set()}, TRADE_DATE)
