from __future__ import annotations

import math

import pytest
from algotrading.core.config import ScenarioConfig
from algotrading.infra.risk import (
    DEFAULT_CONFIDENCE_LEVELS,
    TAIL_RISK_VERSION,
    PositionRisk,
    TailRiskError,
    expected_shortfall,
    position_risk,
    scenario_grid,
    scenario_line_pnls,
    scenario_pnl_distribution,
    scenario_totals,
    tail_risk_from_cells,
    tail_risk_metric,
    tail_risk_report,
    value_at_risk,
)
from fixtures.positions import RISK_VALUATIONS, risk_positions

PNL_TWENTY = (
    100.0,
    90.0,
    80.0,
    70.0,
    60.0,
    50.0,
    40.0,
    30.0,
    20.0,
    10.0,
    0.0,
    -10.0,
    -20.0,
    -30.0,
    -40.0,
    -50.0,
    -100.0,
    -200.0,
    -400.0,
    -800.0,
)


def pf_lines() -> list[PositionRisk]:
    return [
        position_risk(
            portfolio_id="pf-risk",
            quantity=p.quantity,
            valuation=RISK_VALUATIONS[p.contract_key],
        )
        for p in risk_positions()
    ]


def test_var_at_95_is_the_first_percentile_loss_of_twenty_observations() -> None:
    assert value_at_risk(PNL_TWENTY, 0.95) == pytest.approx(800.0)


def test_var_at_90_is_the_second_worst_loss_of_twenty_observations() -> None:
    assert value_at_risk(PNL_TWENTY, 0.90) == pytest.approx(400.0)


def test_var_at_75_is_the_fifth_worst_loss_of_twenty_observations() -> None:
    assert value_at_risk(PNL_TWENTY, 0.75) == pytest.approx(50.0)


def test_es_at_95_is_the_mean_of_the_one_tail_loss() -> None:
    assert expected_shortfall(PNL_TWENTY, 0.95) == pytest.approx(800.0)


def test_es_at_90_is_the_mean_of_the_two_worst_losses() -> None:
    assert expected_shortfall(PNL_TWENTY, 0.90) == pytest.approx((800.0 + 400.0) / 2.0)


def test_es_at_75_is_the_mean_of_the_five_worst_losses() -> None:
    expected = (800.0 + 400.0 + 200.0 + 100.0 + 50.0) / 5.0
    assert expected_shortfall(PNL_TWENTY, 0.75) == pytest.approx(expected)


def test_es_never_understates_var_at_the_same_confidence() -> None:
    for confidence in (0.75, 0.90, 0.95, 0.99):
        es = expected_shortfall(PNL_TWENTY, confidence)
        var = value_at_risk(PNL_TWENTY, confidence)
        assert es >= var - 1e-9


def test_all_gains_distribution_reports_a_negative_var_a_profit_floor() -> None:
    gains = (10.0, 20.0, 30.0, 40.0, 50.0)
    assert value_at_risk(gains, 0.80) == pytest.approx(-10.0)
    assert expected_shortfall(gains, 0.80) == pytest.approx(-10.0)


def test_var_and_es_track_loss_magnitude_not_pnl_sign() -> None:
    losses_only = tuple(-abs(x) for x in (1.0, 2.0, 3.0, 4.0))
    assert value_at_risk(losses_only, 0.75) == pytest.approx(4.0)
    assert expected_shortfall(losses_only, 0.75) == pytest.approx(4.0)


def test_metric_carries_breach_count_and_sample_size() -> None:
    metric = tail_risk_metric(PNL_TWENTY, 0.90)
    assert metric.confidence == 0.90
    assert metric.var == pytest.approx(400.0)
    assert metric.expected_shortfall == pytest.approx(600.0)
    assert metric.breach_count == 2
    assert metric.sample_size == 20


@pytest.mark.parametrize("confidence", [0.0, 1.0, -0.1, 1.5])
def test_confidence_outside_the_open_unit_interval_is_an_error(confidence: float) -> None:
    with pytest.raises(TailRiskError):
        value_at_risk(PNL_TWENTY, confidence)
    with pytest.raises(TailRiskError):
        expected_shortfall(PNL_TWENTY, confidence)


def test_metrics_over_an_empty_distribution_are_an_error_not_a_zero() -> None:
    with pytest.raises(TailRiskError):
        value_at_risk((), 0.95)
    with pytest.raises(TailRiskError):
        expected_shortfall((), 0.95)
    with pytest.raises(TailRiskError):
        tail_risk_report((), confidence_levels=(0.95,))


def test_report_parameterizes_confidence_levels_and_sorts_them() -> None:
    report = tail_risk_report(PNL_TWENTY, confidence_levels=(0.99, 0.90))
    assert report.tail_risk_version == TAIL_RISK_VERSION
    assert report.sample_size == 20
    assert report.worst_loss == pytest.approx(800.0)
    assert tuple(m.confidence for m in report.metrics) == (0.90, 0.99)
    assert report.metrics[0].var == pytest.approx(400.0)
    assert report.metrics[1].var == pytest.approx(800.0)


def test_report_defaults_to_95_and_99() -> None:
    report = tail_risk_report(PNL_TWENTY)
    assert tuple(m.confidence for m in report.metrics) == DEFAULT_CONFIDENCE_LEVELS


def test_report_requires_at_least_one_confidence_level() -> None:
    with pytest.raises(TailRiskError):
        tail_risk_report(PNL_TWENTY, confidence_levels=())


def test_distribution_is_the_per_scenario_portfolio_total_off_the_full_reprice_cells() -> None:
    grid = scenario_grid(
        ScenarioConfig(
            version="v-test",
            spot_shocks=(-0.25, -0.05, 0.05),
            vol_shocks=(0.05,),
            roll_down_days=(1,),
        )
    )
    cells = scenario_line_pnls(pf_lines(), grid)
    totals = scenario_totals(cells)
    distribution = scenario_pnl_distribution(cells)
    assert sorted(distribution) == sorted(totals.values())
    assert len(distribution) == len(totals)


def test_tail_risk_off_cells_matches_metrics_over_the_extracted_distribution() -> None:
    grid = scenario_grid(
        ScenarioConfig(
            version="v-test",
            spot_shocks=(-0.25, -0.05, 0.05),
            vol_shocks=(0.05, -0.05),
            roll_down_days=(1,),
        )
    )
    cells = scenario_line_pnls(pf_lines(), grid)
    distribution = scenario_pnl_distribution(cells)
    report_from_cells = tail_risk_from_cells(cells, confidence_levels=(0.95,))
    report_from_dist = tail_risk_report(distribution, confidence_levels=(0.95,))
    assert report_from_cells.metrics[0].var == pytest.approx(report_from_dist.metrics[0].var)
    assert report_from_cells.metrics[0].expected_shortfall == pytest.approx(
        report_from_dist.metrics[0].expected_shortfall
    )


def test_tail_risk_headline_is_a_loss_on_a_short_left_tail_book() -> None:
    grid = scenario_grid(
        ScenarioConfig(
            version="v-test",
            spot_shocks=(-0.25, -0.05, 0.05),
            vol_shocks=(0.05,),
            roll_down_days=(1,),
        )
    )
    cells = scenario_line_pnls(pf_lines(), grid)
    report = tail_risk_from_cells(cells, confidence_levels=(0.95,))
    worst_scenario_loss = -min(scenario_totals(cells).values())
    assert report.metrics[0].var == pytest.approx(worst_scenario_loss)
    assert report.metrics[0].expected_shortfall >= report.metrics[0].var - 1e-9


def test_distribution_over_no_cells_is_an_error() -> None:
    with pytest.raises(TailRiskError):
        scenario_pnl_distribution([])


def test_es_is_monotone_in_confidence() -> None:
    es_95 = expected_shortfall(PNL_TWENTY, 0.95)
    es_90 = expected_shortfall(PNL_TWENTY, 0.90)
    es_75 = expected_shortfall(PNL_TWENTY, 0.75)
    assert es_95 >= es_90 >= es_75


def test_es_equals_hand_summed_tail_mean_for_a_known_window() -> None:
    losses = sorted((-pnl for pnl in PNL_TWENTY), reverse=True)
    count = math.ceil((1.0 - 0.90) * len(losses))
    expected = sum(losses[:count]) / count
    assert expected_shortfall(PNL_TWENTY, 0.90) == pytest.approx(expected)
