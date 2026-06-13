"""WS 3A — the pure basket->ticket builder and the no-transmission safety invariant.

The builder is a pure mapping (no I/O, no clock, no network): a 2A basket in, a validated
:class:`OrderTicket` out. These tests pin:

* **one-to-one mapping** against a hand-built basket — every ticket leg's side, quantity,
  price spec and grid identity equal values **derived by hand here**, never read back from
  the builder;
* **labelled failures** for every malformed construction (empty basket, duplicate leg, a
  limit with no/!finite/!positive price, an unknown broker/TIF, a price-spec conflict) —
  each a :class:`TicketError` carrying the offending value, never a bare exception;
* **the side mapping** ``long -> BUY`` / ``short -> SELL`` with a positive (magnitude)
  quantity — the basket's signed quantity is the source, never re-applied;
* **the safety gate, made falsifiable** — the orders module reads no credential/env token and
  imports no broker-submission symbol; 3A cannot transmit by construction.

Independent oracle: the expected ticket below is written out by hand from the basket inputs;
the builder's output is compared to it, not used to define it.
"""

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

# A hand-built basket: a long option leg (3m ATM) and a short stock hedge on the same index.
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

    # Envelope: provenance + scope come straight from the basket; paper by construction.
    assert ticket.source_basket_id == "B-1"
    assert ticket.trade_date == TRADE_DATE
    assert ticket.underlying == "SX5E"
    assert ticket.target_broker is TargetBroker.IBKR
    assert ticket.time_in_force is TimeInForce.DAY
    assert ticket.mode == "paper"
    assert len(ticket.legs) == 2

    # Leg 1 (hand-derived): long option -> BUY, magnitude 2, default Market, grid identity kept.
    option_leg = ticket.legs[0]
    assert option_leg.instrument_kind == "option"
    assert option_leg.underlying == "SX5E"
    assert option_leg.side is Side.BUY
    assert option_leg.quantity == 2.0
    assert isinstance(option_leg.price_spec, Market)
    assert (option_leg.tenor_label, option_leg.delta_band) == ("3m", "ATM")

    # Leg 2 (hand-derived): short stock -> SELL, magnitude 5 (abs of -5), no tenor/band.
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
    # "a limit with no price" is unrepresentable (price is required); these are the rest.
    with pytest.raises(TicketError) as exc:
        Limit(bad_price)
    assert exc.value.field == "price"


def test_market_carries_no_price() -> None:
    # The closed set: a Market has no price attribute to set (invalid-by-construction guarantee).
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
        build_ticket(BASKET, price_spec_by_leg=[Market()])  # basket has 2 legs
    assert exc.value.field == "price_spec_by_leg"


def test_target_broker_resolves_to_existing_adapter() -> None:
    # IBKR is the sole live broker (ADR 0042). It resolves; the ticket names it, nothing connects.
    assert TargetBroker.IBKR.value == "ibkr"
    assert build_ticket(BASKET, broker=TargetBroker.IBKR).target_broker is TargetBroker.IBKR


# --- The safety gate, made a falsifiable test ------------------------------------------------

# Identifiers (not prose) that would betray a transmission path or a credential read. We scan the
# AST, so the module's *docstrings* ("reads no credential", "no transmission") never trip it — only
# real code does: an imported broker module, a getenv call, a submit/place/transmit symbol.
_FORBIDDEN_NAMES = frozenset({
    "environ", "getenv", "load_dotenv", "api_key", "credential", "password", "secret",
    "transmit", "place_order", "submit_order", "send_order", "BrokerTransport",
})
_FORBIDDEN_IMPORT_SUBSTRINGS = ("infra_ibkr", "connectivity", "dotenv")


def _orders_code_names() -> tuple[set[str], set[str]]:
    """Every identifier and every imported-module path used in the orders package's code."""
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
    # No broker/submission/credential symbol in the code, and no env/`os` import.
    assert not (names & _FORBIDDEN_NAMES), f"forbidden symbol(s): {names & _FORBIDDEN_NAMES}"
    assert "os" not in imports, "the orders module must not import os (no env reads)"
    leaked = [m for m in imports for s in _FORBIDDEN_IMPORT_SUBSTRINGS if s in m]
    assert leaked == [], f"orders module must not import: {leaked}"
