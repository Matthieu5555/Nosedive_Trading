from __future__ import annotations

from datetime import date

import pytest
from algotrading.infra.contracts import InstrumentKey
from algotrading.infra.universe import (
    AvailableChain,
    ChainSelection,
    bracket_dates,
    plan_chain,
    select_capture_keys,
    select_expiries,
    select_expiries_bracketing,
    tenor_target_dates,
)

_TENOR_YEARS = (10.0 / 365.0, 1.0 / 12.0, 3.0 / 12.0, 6.0 / 12.0, 1.0, 1.5, 2.0, 3.0)
_AS_OF = date(2026, 6, 11)


def test_tenor_target_dates_are_act365_from_as_of() -> None:
    got = tenor_target_dates(_AS_OF, _TENOR_YEARS)
    deltas = [(target - _AS_OF).days for target in got]
    assert deltas == [10, 30, 91, 182, 365, 548, 730, 1095]
    assert got[-1] == date(2029, 6, 10)


def test_tenor_target_dates_skips_non_finite_and_dedups() -> None:
    got = tenor_target_dates(_AS_OF, [0.25, 0.25, float("nan"), -1.0, 0.0])
    assert got == (date(2026, 9, 10),)


def test_bracket_straddles_target() -> None:
    listed = [date(2026, 6, 12), date(2026, 6, 19), date(2026, 9, 10), date(2026, 12, 18)]
    assert bracket_dates(listed, [date(2026, 7, 1)]) == (date(2026, 6, 19), date(2026, 9, 10))


def test_bracket_on_listed_date_selects_that_one() -> None:
    listed = [date(2026, 6, 12), date(2026, 9, 10), date(2026, 12, 18)]
    assert bracket_dates(listed, [date(2026, 9, 10)]) == (date(2026, 9, 10),)


def test_bracket_past_the_end_is_one_sided() -> None:
    listed = [date(2026, 6, 19), date(2027, 6, 18)]
    assert bracket_dates(listed, [date(2029, 6, 10)]) == (date(2027, 6, 18),)
    assert bracket_dates(listed, [date(2025, 1, 1)]) == (date(2026, 6, 19),)


def test_select_expiries_bracketing_spans_term_structure_and_dedups() -> None:
    listed = [
        "20260612", "20260619", "20260717", "20260918", "20261218",
        "20270618", "20271217", "20281215", "20291221",
    ]
    got = select_expiries_bracketing(listed, as_of=_AS_OF, tenor_years=_TENOR_YEARS)
    assert got == (
        "20260619", "20260717", "20260918", "20261218",
        "20270618", "20271217", "20281215", "20291221",
    )
    assert got == tuple(sorted(set(got)))
    assert "20260612" not in got
    assert got[-1] == "20291221"


def test_no_frontload_collapse_when_longer_expiries_exist() -> None:
    june = [f"202606{day:02d}" for day in (10, 11, 12, 15, 16, 17, 18, 22)]
    longer = ["20260918", "20261218", "20270618", "20281215", "20291221"]
    listed = june + longer
    nearest8 = select_expiries(listed, 8)
    assert all(token.startswith("202606") for token in nearest8)
    got = select_expiries_bracketing(
        listed, as_of=_AS_OF, tenor_years=(10 / 365, 1 / 12, 3 / 12, 6 / 12, 1.0, 2.0, 3.0)
    )
    assert any(token >= "20281215" for token in got)
    assert got[-1] == "20291221"


def test_bracketing_is_reorder_invariant() -> None:
    listed = ["20260918", "20260619", "20291221", "20270618"]
    tenors = (3 / 12, 1.0, 3.0)
    forward = select_expiries_bracketing(listed, as_of=_AS_OF, tenor_years=tenors)
    reverse = select_expiries_bracketing(list(reversed(listed)), as_of=_AS_OF, tenor_years=tenors)
    assert forward == reverse == tuple(sorted(forward))


def test_bracketing_edge_cases() -> None:
    tenors = (0.25, 1.0)
    assert select_expiries_bracketing([], as_of=_AS_OF, tenor_years=tenors) == ()
    assert select_expiries_bracketing(
        ["nonsense", "2026", "", "2026013x", "20260619"], as_of=_AS_OF, tenor_years=tenors
    ) == ("20260619",)
    assert select_expiries_bracketing(["20260619"], as_of=_AS_OF, tenor_years=tenors) == ("20260619",)


def test_chain_selection_targeting_flag_and_validation() -> None:
    assert not ChainSelection().targets_tenors
    assert not ChainSelection(tenor_years=(0.25,)).targets_tenors
    assert ChainSelection(tenor_years=(0.25,), as_of=_AS_OF).targets_tenors
    with pytest.raises(ValueError):
        ChainSelection(tenor_years=(0.0,), as_of=_AS_OF)
    with pytest.raises(ValueError):
        ChainSelection(tenor_years=(float("nan"),), as_of=_AS_OF)


def test_plan_chain_brackets_when_targeted_else_nearest_n() -> None:
    chain = AvailableChain(
        exchange="SMART", trading_class="SPX", multiplier="100",
        expirations=("20260612", "20260619", "20260918", "20281215", "20291221"),
        strikes=(90.0, 100.0, 110.0),
    )
    targeted = plan_chain(
        "SPX", [chain], spot=100.0,
        selection=ChainSelection(tenor_years=(3 / 12, 3.0), as_of=_AS_OF, min_strikes_per_side=1),
    )
    assert targeted is not None
    assert set(targeted.expiries) == {"20260619", "20260918", "20281215", "20291221"}
    legacy = plan_chain(
        "SPX", [chain], spot=100.0, selection=ChainSelection(max_expiries=2, min_strikes_per_side=1)
    )
    assert legacy is not None
    assert legacy.expiries == ("20260612", "20260619")


def _option(expiry: date) -> InstrumentKey:
    return InstrumentKey("SPX", "OPT", "CBOE", "USD", 100.0, f"c-{expiry}", expiry, 100.0, "C")


def test_select_capture_keys_brackets_the_streamed_set() -> None:
    underlying = InstrumentKey("SPX", "IND", "CBOE", "USD", 1.0, "c-spx")
    options = [
        _option(date(2026, 6, 19)), _option(date(2026, 9, 18)), _option(date(2026, 12, 18)),
        _option(date(2027, 6, 18)), _option(date(2028, 12, 15)), _option(date(2029, 12, 21)),
    ]
    selection = ChainSelection(tenor_years=(3 / 12, 3.0), as_of=_AS_OF, min_strikes_per_side=1)
    captured = set(
        select_capture_keys(
            [underlying, *options], spots={"SPX": 100.0}, selection=selection, exchange="CBOE"
        )
    )
    kept = {key.expiry for key in options if key.canonical() in captured}
    assert kept == {date(2026, 6, 19), date(2026, 9, 18), date(2028, 12, 15), date(2029, 12, 21)}
