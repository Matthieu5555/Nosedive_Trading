from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import pytest
from algotrading.infra.contracts import Basket, BasketLeg
from algotrading.infra.orders import (
    Limit,
    Market,
    Side,
    TargetBroker,
    TicketError,
    TimeInForce,
    build_ticket,
)
from algotrading.infra.orders import ticket as ticket_module

TRADE_DATE = date(2026, 6, 12)
BASKET = Basket(
    basket_id="B-1",
    trade_date=TRADE_DATE,
    underlying="SX5E",
    legs=(
        BasketLeg(
            instrument_kind="option",
            side="long",
            quantity=2.0,
            underlying="SX5E",
            tenor_label="3m",
            delta_band="ATM",
        ),
        BasketLeg(
            instrument_kind="stock",
            side="short",
            quantity=-5.0,
            underlying="SX5E",
        ),
    ),
)


def test_build_ticket_maps_basket_legs_one_to_one() -> None:
    ticket = build_ticket(BASKET)

    assert ticket.source_basket_id == "B-1"
    assert ticket.trade_date == TRADE_DATE
    assert ticket.underlying == "SX5E"
    assert ticket.target_broker is TargetBroker.IBKR
    assert ticket.time_in_force is TimeInForce.DAY
    assert ticket.mode == "paper"
    assert len(ticket.legs) == 2

    option_leg = ticket.legs[0]
    assert option_leg.instrument_kind == "option"
    assert option_leg.underlying == "SX5E"
    assert option_leg.side is Side.BUY
    assert option_leg.quantity == 2.0
    assert isinstance(option_leg.price_spec, Market)
    assert (option_leg.tenor_label, option_leg.delta_band) == ("3m", "ATM")

    stock_leg = ticket.legs[1]
    assert stock_leg.instrument_kind == "stock"
    assert stock_leg.side is Side.SELL
    assert stock_leg.quantity == 5.0
    assert (stock_leg.tenor_label, stock_leg.delta_band) == (None, None)


def test_build_ticket_applies_per_leg_price_specs() -> None:
    ticket = build_ticket(BASKET, price_spec_by_leg=[Limit(123.5), Market()])
    assert isinstance(ticket.legs[0].price_spec, Limit)
    assert ticket.legs[0].price_spec.price == 123.5
    assert isinstance(ticket.legs[1].price_spec, Market)


def test_build_ticket_uniform_limit_applies_to_every_leg() -> None:
    ticket = build_ticket(BASKET, price_spec=Limit(10.0))
    assert all(isinstance(leg.price_spec, Limit) for leg in ticket.legs)


def test_build_ticket_rejects_empty_basket() -> None:
    empty = Basket(basket_id="B-0", trade_date=TRADE_DATE, underlying="SX5E", legs=())
    with pytest.raises(TicketError) as exc:
        build_ticket(empty)
    assert exc.value.field == "legs"


def test_build_ticket_rejects_duplicate_leg() -> None:
    leg = BasketLeg(
        instrument_kind="option", side="long", quantity=1.0,
        underlying="SX5E", tenor_label="3m", delta_band="ATM",
    )
    dup = Basket(basket_id="B-2", trade_date=TRADE_DATE, underlying="SX5E", legs=(leg, leg))
    with pytest.raises(TicketError) as exc:
        build_ticket(dup)
    assert exc.value.field == "legs"
    assert "duplicate" in exc.value.reason


@pytest.mark.parametrize("bad_price", [0.0, -1.0, math.inf, math.nan])
def test_limit_rejects_non_positive_or_non_finite_price(bad_price: float) -> None:
    with pytest.raises(TicketError) as exc:
        Limit(bad_price)
    assert exc.value.field == "price"


def test_market_carries_no_price() -> None:
    assert not hasattr(Market(), "price")


def test_build_ticket_rejects_unknown_broker() -> None:
    with pytest.raises(TicketError) as exc:
        build_ticket(BASKET, broker="ibkr")  # type: ignore[arg-type]  # a string is not a TargetBroker
    assert exc.value.field == "broker"


def test_build_ticket_rejects_unknown_tif() -> None:
    with pytest.raises(TicketError) as exc:
        build_ticket(BASKET, tif="day")  # type: ignore[arg-type]
    assert exc.value.field == "tif"


def test_build_ticket_rejects_price_spec_conflict() -> None:
    with pytest.raises(TicketError) as exc:
        build_ticket(BASKET, price_spec=Market(), price_spec_by_leg=[Market(), Market()])
    assert exc.value.field == "price_spec"


def test_build_ticket_rejects_price_spec_by_leg_length_mismatch() -> None:
    with pytest.raises(TicketError) as exc:
        build_ticket(BASKET, price_spec_by_leg=[Market()])
    assert exc.value.field == "price_spec_by_leg"


def test_target_broker_resolves_to_existing_adapter() -> None:
    assert TargetBroker.IBKR.value == "ibkr"
    assert build_ticket(BASKET, broker=TargetBroker.IBKR).target_broker is TargetBroker.IBKR


_FORBIDDEN_NAMES = frozenset({
    "environ", "getenv", "load_dotenv", "api_key", "credential", "password", "secret",
    "transmit", "place_order", "submit_order", "send_order", "BrokerTransport",
})
_FORBIDDEN_IMPORT_SUBSTRINGS = ("infra_ibkr", "connectivity", "dotenv")


def _orders_code_names() -> tuple[set[str], set[str]]:
    import ast

    pkg_dir = Path(ticket_module.__file__).parent
    names: set[str] = set()
    imports: set[str] = set()
    for path in sorted(pkg_dir.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module or "")
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.Name):
                names.add(node.id)
    return names, imports


def test_ticket_path_never_transmits_and_reads_no_credentials() -> None:
    names, imports = _orders_code_names()
    assert not (names & _FORBIDDEN_NAMES), f"forbidden symbol(s): {names & _FORBIDDEN_NAMES}"
    assert "os" not in imports, "the orders module must not import os (no env reads)"
    leaked = [m for m in imports for s in _FORBIDDEN_IMPORT_SUBSTRINGS if s in m]
    assert leaked == [], f"orders module must not import: {leaked}"
