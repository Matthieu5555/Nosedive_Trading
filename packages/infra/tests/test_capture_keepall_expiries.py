from __future__ import annotations

from datetime import date, timedelta

from algotrading.infra.contracts import InstrumentKey
from algotrading.infra.universe import (
    AvailableChain,
    ChainSelection,
    plan_chain,
    select_capture_keys,
    select_expiries,
)

_AS_OF = date(2026, 6, 11)
_LEAPS = (date(2028, 12, 15), date(2029, 6, 15), date(2029, 12, 21))


def _over_64_expiry_dates() -> list[date]:
    weeklies = [_AS_OF + timedelta(days=7 * step) for step in range(1, 71)]
    return sorted(set(weeklies) | set(_LEAPS))


def _token(value: date) -> str:
    return value.strftime("%Y%m%d")


def _production_default_selection() -> ChainSelection:
    return ChainSelection(max_expiries=None, min_strikes_per_side=1)


def test_fixture_exceeds_the_old_64_budget_and_leaps_sit_past_it() -> None:
    dates = _over_64_expiry_dates()
    assert len(dates) == 73
    nearest_64 = dates[:64]
    for leap in _LEAPS:
        assert leap not in nearest_64


def test_nearest_n_at_64_would_drop_the_leaps() -> None:
    dates = _over_64_expiry_dates()
    truncated = select_expiries([_token(value) for value in dates], 64)
    for leap in _LEAPS:
        assert _token(leap) not in truncated


def test_keepall_default_survives_the_leaps_through_plan_chain() -> None:
    dates = _over_64_expiry_dates()
    chain = AvailableChain(
        exchange="SMART",
        trading_class="SPX",
        multiplier="100",
        expirations=tuple(_token(value) for value in dates),
        strikes=(90.0, 100.0, 110.0),
    )
    plan = plan_chain(
        "SPX", [chain], spot=100.0, selection=_production_default_selection()
    )
    assert plan is not None
    assert len(plan.expiries) == len(dates)
    for leap in _LEAPS:
        assert _token(leap) in plan.expiries


def _option(expiry: date) -> InstrumentKey:
    return InstrumentKey(
        "SPX", "OPT", "CBOE", "USD", 100.0, f"c-{expiry}", expiry, 100.0, "C"
    )


def test_keepall_default_survives_the_leaps_through_select_capture_keys() -> None:
    dates = _over_64_expiry_dates()
    underlying = InstrumentKey("SPX", "IND", "CBOE", "USD", 1.0, "c-spx")
    options = [_option(value) for value in dates]
    captured = set(
        select_capture_keys(
            [underlying, *options],
            spots={"SPX": 100.0},
            selection=_production_default_selection(),
            exchange="CBOE",
        )
    )
    kept = {key.expiry for key in options if key.canonical() in captured}
    assert kept == set(dates)
    for leap in _LEAPS:
        assert leap in kept
