from __future__ import annotations

from datetime import date

from algotrading.infra.surfaces import tenor_years
from algotrading.infra.universe import ChainSelection
from algotrading.infra_ibkr.collectors.cp_rest_chain_window import (
    parse_month_token,
    select_discovery_months,
)

_AS_OF = date(2026, 6, 11)
_GRID = ("10d", "1m", "3m", "6m", "12m", "18m", "2y", "3y")


def _targeted_selection() -> ChainSelection:
    return ChainSelection(
        tenor_years=tuple(tenor_years(label) for label in _GRID), as_of=_AS_OF
    )


def test_parse_month_token() -> None:
    assert parse_month_token("DEC28") == date(2028, 12, 15)
    assert parse_month_token("jun26") == date(2026, 6, 15)
    assert parse_month_token("XXX99") is None
    assert parse_month_token("JUN261") is None
    assert parse_month_token("JUN2") is None
    assert parse_month_token("") is None


def test_discovery_months_bracket_to_the_long_end() -> None:
    months = (
        "JUN26", "JUL26", "SEP26", "DEC26", "JUN27", "SEP27", "DEC27",
        "DEC28", "DEC29", "DEC30", "DEC31",
    )
    got = select_discovery_months(months, _targeted_selection())
    assert got == (
        "JUN26", "JUL26", "SEP26", "DEC26", "JUN27", "SEP27", "DEC27", "DEC28", "DEC29",
    )
    assert "DEC30" not in got and "DEC31" not in got


def test_discovery_months_legacy_slice_when_untargeted() -> None:
    months = ("JUN26", "JUL26", "SEP26", "DEC26", "MAR27")
    got = select_discovery_months(months, ChainSelection(max_expiries=3))
    assert got == ("JUN26", "JUL26", "SEP26")


def test_discovery_months_unparseable_falls_back_to_legacy() -> None:
    months = ("garbage", "still-bad")
    got = select_discovery_months(months, _targeted_selection())
    assert got == ("garbage", "still-bad")
