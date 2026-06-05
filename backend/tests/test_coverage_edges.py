"""Focused edge tests that keep pure-core coverage honest.

These cases cover defensive branches and derived properties that the behavior suites
did not naturally hit. They are intentionally small and named: each assertion pins a
real edge contract rather than exercising code only for a coverage number.
"""

from __future__ import annotations

import dataclasses

import pytest

from config import ScenarioConfig
from fixtures.events import SNAPSHOT_TS, UNDERLYING, event
from fixtures.positions import CALL_100
from forwards.estimate import ForwardPair
from risk import (
    AggregationError,
    Scenario,
    ScenarioGridError,
    full_reprice_pnl,
    group_key_for,
    position_risk,
    scenario_grid,
    scenario_line_pnls,
    scenario_totals,
)
from snapshots import (
    assess_quote,
    check_open_interest,
    check_price_against_intrinsic,
    latest_by_field_before,
)


def test_as_of_tie_breaks_same_timestamp_by_event_id() -> None:
    """Same-field, same-time events choose the larger event id deterministically."""
    older_id = event(UNDERLYING, "bid", 100.0, ts=SNAPSHOT_TS, event_id="evt-1")
    newer_id = event(UNDERLYING, "bid", 101.0, ts=SNAPSHOT_TS, event_id="evt-2")

    latest = latest_by_field_before((newer_id, older_id), SNAPSHOT_TS)

    assert latest["bid"] == newer_id


def test_quote_optional_checks_return_cleanly_at_boundaries() -> None:
    """Optional quote checks produce no finding when supplied values are in bounds."""
    assert check_open_interest(10.0, 10.0) is None
    assert check_price_against_intrinsic(5.0, intrinsic=5.0, max_value=10.0) is None
    assert check_price_against_intrinsic(10.0, intrinsic=5.0, max_value=10.0) is None

    verdict = assess_quote(
        bid=9.9,
        ask=10.1,
        max_spread_pct=0.05,
        age_seconds=30.0,
        max_quote_age_seconds=30.0,
        open_interest=10.0,
        min_open_interest=10.0,
        price=5.0,
        intrinsic=5.0,
        max_value=10.0,
    )
    assert verdict.status == "usable"
    assert verdict.reasons == ()


def test_forward_outlier_guard_keeps_points_when_rejection_would_starve_fit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outlier rejection is skipped if it would leave fewer than two strikes."""
    import forwards.estimate as estimate_module

    works = [
        estimate_module._Work(
            ForwardPair(90.0, 12.0, 2.0, 1.0, "c90", "p90"), parity_spread=10.0
        ),
        estimate_module._Work(
            ForwardPair(100.0, 7.0, 7.0, 1.0, "c100", "p100"), parity_spread=0.0
        ),
        estimate_module._Work(
            ForwardPair(110.0, 2.0, 12.0, 1.0, "c110", "p110"), parity_spread=-10.0
        ),
    ]

    monkeypatch.setattr(
        estimate_module,
        "outlier_flags",
        lambda _residuals, *, scale_floor: (True, True, False),
    )

    estimate_module._flag_outliers(works)

    assert [work.rejected for work in works] == [False, False, False]


def test_position_market_value_and_invalid_group_dimension_are_explicit() -> None:
    line = position_risk(portfolio_id="pf", quantity=2.0, valuation=CALL_100)

    assert line.market_value == pytest.approx(line.greeks.price * line.scale)
    with pytest.raises(AggregationError) as info:
        group_key_for(line, "currency")
    assert info.value.dimension == "currency"


def test_zero_maturity_valuation_has_defined_forward_and_implied_rate() -> None:
    valuation = dataclasses.replace(CALL_100, maturity_years=0.0, discount_factor=1.0)

    assert valuation.forward == pytest.approx(valuation.spot)
    assert valuation.implied_rate == 0.0


def test_scenario_grid_rejects_formatted_id_collisions() -> None:
    """Distinct tiny shocks can format to the same stable id and must be rejected."""
    config = ScenarioConfig(
        version="scn-collision", spot_shocks=(0.00001, 0.00002), vol_shocks=()
    )

    with pytest.raises(ScenarioGridError, match="colliding ids"):
        scenario_grid(config)


def test_scenario_totals_accumulate_in_insertion_order() -> None:
    line = position_risk(portfolio_id="pf", quantity=1.0, valuation=CALL_100)
    scenarios = (
        Scenario("down", "spot", -0.01, 0.0, 0.0),
        Scenario("up", "spot", 0.01, 0.0, 0.0),
        Scenario("down", "spot", -0.02, 0.0, 0.0),
    )
    cells = [
        dataclasses.replace(cell, full_reprice_pnl=float(index + 1))
        for index, cell in enumerate(scenario_line_pnls((line,), scenarios))
    ]

    assert scenario_totals(cells) == {"down": 4.0, "up": 2.0}


def test_full_reprice_accepts_an_explicit_step_count() -> None:
    line = position_risk(portfolio_id="pf", quantity=1.0, valuation=CALL_100)
    scenario = Scenario("roll", "time", 0.0, 0.0, 1.0 / 365.0)

    stepped = full_reprice_pnl(line, scenario, steps=3)
    default = full_reprice_pnl(line, scenario)

    assert stepped == pytest.approx(default)

