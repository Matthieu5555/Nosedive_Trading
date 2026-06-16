"""A constructed options book for the risk dashboard.

There is no positions / fills / P&L table in the offline store, so the dashboard
has nothing real to attribute or concentrate. This module *invents* a plausible
multi-name options book — a vol-seller that owns crash protection — so the
book-dependent views (P&L attribution, greek ladder, concentration, scenario
stress) have a coherent position to key off. The math that runs on this book is
the real, tested risk engine; only the positions are synthetic.

The book is built from each underlying's *actually available* grid cells, so it
resolves cleanly against the banked analytics instead of pointing at cells that
were never captured (thin names like SAF/TTE simply contribute fewer legs).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from algotrading.infra.contracts import Basket, BasketLeg

# Per role, the delta bands we prefer, best first. _pick walks this order and
# takes the first band the name actually has a cell for.
_ATM_BANDS = ("atm", "atmp")
_PUT_WING_BANDS = ("10dp", "12dp", "08dp", "14dp", "20dp", "06dp", "30dp")
_CALL_WING_BANDS = ("10dc", "12dc", "08dc", "14dc", "20dc", "06dc", "30dc")
# Maturity we prefer for a leg, best first.
_TENOR_PREF = ("1m", "3m", "6m", "2m", "9m", "12m", "10d", "18m")

Cell = tuple[str, str]  # (tenor_label, delta_band)


def _pick(cells: set[Cell], bands: tuple[str, ...]) -> Cell | None:
    """Pick (tenor, band) for the most-preferred band the name actually has."""
    for band in bands:
        tenors = [t for (t, b) in cells if b == band]
        if not tenors:
            continue
        for preferred in _TENOR_PREF:
            if preferred in tenors:
                return (preferred, band)
        return (sorted(tenors)[0], band)
    return None


def _leg(underlying: str, cell: Cell, side: str, qty: int) -> BasketLeg:
    tenor, band = cell
    signed = float(qty) if side == "long" else -float(qty)
    return BasketLeg(
        instrument_kind="option",
        side=side,
        quantity=signed,
        underlying=underlying,
        tenor_label=tenor,
        delta_band=band,
    )


def build_book(
    cells_by_underlying: Mapping[str, set[Cell]],
    trade_date: date,
    *,
    basket_id: str = "pm-demo-book",
    primary: str = "SX5E",
) -> Basket:
    """Build the constructed book from the cells each name actually has.

    The shape, per name, is at most three legs:
      - an at-the-money leg (sold on even-indexed names, bought on odd) — the
        vol view,
      - a downside put wing, always long — the crash hedge every name carries,
      - a call wing, sold alongside a short ATM (covered upside) or bought with
        a long ATM.

    Quantities fan out by name index so the book is not uniform, which keeps the
    greek ladder, concentration and scenario surface from being degenerate.
    """
    legs: list[BasketLeg] = []
    for idx, underlying in enumerate(sorted(cells_by_underlying)):
        cells = cells_by_underlying[underlying]
        if not cells:
            continue
        sells_vol = idx % 2 == 0
        scale = 10 + 5 * idx

        atm = _pick(cells, _ATM_BANDS)
        if atm is not None:
            legs.append(_leg(underlying, atm, "short" if sells_vol else "long", scale))

        put_wing = _pick(cells, _PUT_WING_BANDS)
        if put_wing is not None:
            legs.append(_leg(underlying, put_wing, "long", max(5, scale // 2)))

        call_wing = _pick(cells, _CALL_WING_BANDS)
        if call_wing is not None:
            legs.append(
                _leg(underlying, call_wing, "short" if sells_vol else "long", max(5, scale // 3))
            )

    if not legs:
        raise ValueError("no resolvable cells supplied — cannot build a book")

    return Basket(
        basket_id=basket_id,
        trade_date=trade_date,
        underlying=primary,
        legs=tuple(legs),
    )
