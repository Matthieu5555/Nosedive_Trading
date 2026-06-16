from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from algotrading.core.config import ScenarioConfig
from algotrading.core.provenance import ProvenanceStamp, source_ref
from algotrading.infra.contracts import RiskAggregate, ScenarioResult
from algotrading.infra.risk import (
    AggregationError,
    BrokerGreeks,
    Position,
    PositionSet,
    RiskParams,
    Scenario,
    aggregate_by_key,
    aggregate_lines,
    basket_variance,
    build_risk_snapshot,
    build_scenario_report,
    effective_scenario_version,
    hypothetical_positions,
    position_risk,
    reconcile_report,
    resolve_grouping_key,
    risk_aggregate,
    scenario_grid,
    scenario_line_pnls,
    scenario_result,
)
from fixtures.positions import RISK_VALUATIONS, risk_positions
from fixtures.records import make_stamp

TS = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)


def _stamp(keys: tuple[str, ...]) -> ProvenanceStamp:
    return make_stamp(tuple(source_ref("market_state_snapshots", TS, k) for k in keys))


def _pf_lines() -> list:
    return [
        position_risk(portfolio_id="pf-risk", quantity=p.quantity, valuation=RISK_VALUATIONS[p.contract_key])
        for p in risk_positions()
    ]


def test_basket_variance_matches_hand_derived_oracle() -> None:
    result = basket_variance([0.6, 0.4], [0.2, 0.3], avg_correlation=0.5)
    assert result.variance == pytest.approx(0.0432, abs=1e-12)
    assert result.vol == pytest.approx(math.sqrt(0.0432), rel=1e-12)
    assert result.diversification_ratio == pytest.approx(math.sqrt(0.0432) / 0.24, rel=1e-12)


def test_basket_full_matrix_equals_average_correlation_form() -> None:
    avg = basket_variance([0.6, 0.4], [0.2, 0.3], avg_correlation=0.5)
    full = basket_variance(
        [0.6, 0.4], [0.2, 0.3], correlations=[[1.0, 0.5], [0.5, 1.0]]
    )
    assert full.variance == pytest.approx(avg.variance, rel=1e-12)


def test_basket_zero_correlation_diversifies_by_inverse_sqrt_n() -> None:
    result = basket_variance([0.5, 0.5], [0.2, 0.2], avg_correlation=0.0)
    assert result.diversification_ratio == pytest.approx(1.0 / math.sqrt(2.0), rel=1e-12)


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"avg_correlation": 0.5, "correlations": [[1.0, 0.5], [0.5, 1.0]]},
    ],
)
def test_basket_requires_exactly_one_correlation_input(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        basket_variance([0.6, 0.4], [0.2, 0.3], **kwargs)


def test_basket_rejects_mismatched_shapes_and_nonsquare_matrix() -> None:
    with pytest.raises(ValueError):
        basket_variance([0.6, 0.4], [0.2], avg_correlation=0.5)
    with pytest.raises(ValueError):
        basket_variance([0.6, 0.4], [0.2, 0.3], correlations=[[1.0, 0.5]])


def test_position_validates_key_and_quantity() -> None:
    Position("AAPL|OPT|C|100", Decimal("10"))
    with pytest.raises(ValueError):
        Position("  ", Decimal("10"))
    with pytest.raises(ValueError):
        Position("AAPL|OPT|C|100", Decimal("0"))
    with pytest.raises(ValueError):
        Position("AAPL|OPT|C|100", Decimal("nan"))


def test_position_set_and_hypothetical_book() -> None:
    with pytest.raises(ValueError):
        PositionSet(positions=(), source="  ", source_ts=TS)
    book = hypothetical_positions(
        [Position("AAPL|OPT|C|100", Decimal("10"), tags={"desk": "vol"})], source_ts=TS
    )
    assert book.source == "hypothetical"
    assert book.source_ts == TS
    assert book.positions[0].tags == {"desk": "vol"}


def test_risk_params_defaults_and_from_mapping() -> None:
    defaults = RiskParams.defaults()
    assert defaults.grouping_keys == ("underlying", "maturity", "instrument")
    params = RiskParams.from_mapping(
        {"grouping_keys": ["underlying", "desk"], "reconciliation_tolerances": {"delta": 0.005},
         "version": "risk-cfg-2"}
    )
    assert params.grouping_keys == ("underlying", "desk")
    assert params.reconciliation_tolerance.delta == 0.005
    assert params.reconciliation_tolerance.vega == defaults.reconciliation_tolerance.vega
    assert params.config_version == "risk-cfg-2"


def test_risk_params_rejects_empty_and_unknown_keys() -> None:
    with pytest.raises(ValueError):
        RiskParams(grouping_keys=(), reconciliation_tolerance=RiskParams.defaults().reconciliation_tolerance, config_version="v")
    with pytest.raises(AggregationError):
        RiskParams.from_mapping({"grouping_keys": ["sector"]})


def test_aggregate_by_key_dispatches_and_validates() -> None:
    lines = _pf_lines()
    by_instrument = aggregate_by_key(lines, portfolio_id="pf-risk", key="instrument")
    assert len(by_instrument) == 3
    desk_of = {line.contract_key: "vol" for line in lines}
    by_desk = aggregate_by_key(lines, portfolio_id="pf-risk", key="desk", desk_of=desk_of)
    assert [g.group_key for g in by_desk] == ["desk:vol"]
    with pytest.raises(AggregationError):
        aggregate_by_key(lines, portfolio_id="pf-risk", key="desk")
    with pytest.raises(AggregationError):
        aggregate_by_key(lines, portfolio_id="pf-risk", key="sector")


def test_resolve_grouping_key_validates_names() -> None:
    assert resolve_grouping_key("underlying").__name__ == "aggregate_lines"
    assert resolve_grouping_key("desk").__name__ == "aggregate_by_desk"
    with pytest.raises(AggregationError):
        resolve_grouping_key("sector")


def test_risk_aggregate_projects_into_frozen_contract() -> None:
    net = aggregate_lines(_pf_lines(), portfolio_id="pf-risk", dimension="underlying")[0]
    agg = risk_aggregate(net, valuation_ts=TS, source_snapshot_ts=TS, provenance=_stamp(("AAPL|OPT|C|100",)))
    assert isinstance(agg, RiskAggregate)
    assert agg.group_key == "underlying:AAPL"
    assert agg.net_delta == net.net_delta


def _book() -> PositionSet:
    return hypothetical_positions(
        [
            Position("AAPL|OPT|C|100", Decimal("10")),
            Position("AAPL|OPT|P|100", Decimal("-5")),
            Position("AAPL|OPT|C|105", Decimal("3")),
        ],
        source_ts=TS,
        source="book-1",
    )


def test_build_risk_snapshot_aggregates_and_stamps() -> None:
    snap = build_risk_snapshot(
        _book(), RISK_VALUATIONS, RiskParams.defaults(),
        analytics_version="ana-1", portfolio_id="pf-risk",
    )
    assert len(snap.lines) == 3
    assert {g.key for g in snap.aggregations} == {"underlying", "maturity", "instrument"}
    under = snap.grouped("underlying")
    assert len(under) == 1
    assert under[0].net_delta == pytest.approx(sum(line.position_delta for line in snap.lines))
    assert snap.analytics_version == "ana-1"
    assert snap.position_source == "book-1"
    assert snap.position_source_ts == TS
    assert snap.reconciliation is None


def test_build_risk_snapshot_reconciles_when_broker_greeks_supplied() -> None:
    book = _book()
    snap = build_risk_snapshot(
        book, RISK_VALUATIONS, RiskParams.defaults(),
        analytics_version="ana-1", portfolio_id="pf-risk",
        broker_greeks={"AAPL|OPT|C|100": BrokerGreeks(contract_key="AAPL|OPT|C|100", delta=0.0)},
    )
    assert snap.reconciliation is not None
    assert not snap.reconciliation.ok
    assert snap.reconciliation.compared == 1


def test_build_risk_snapshot_missing_valuation_is_named_not_dropped() -> None:
    from algotrading.infra.risk import MissingValuationError

    book = hypothetical_positions([Position("AAPL|OPT|C|999", Decimal("1"))], source_ts=TS)
    with pytest.raises(MissingValuationError):
        build_risk_snapshot(
            book, RISK_VALUATIONS, RiskParams.defaults(),
            analytics_version="ana-1", portfolio_id="pf-risk",
        )


def test_build_risk_snapshot_desk_grouping_uses_mapping() -> None:
    book = _book()
    params = RiskParams(
        grouping_keys=("desk",),
        reconciliation_tolerance=RiskParams.defaults().reconciliation_tolerance,
        config_version="v",
    )
    snap = build_risk_snapshot(
        book, RISK_VALUATIONS, params, analytics_version="ana-1", portfolio_id="pf-risk",
        desk_of={"AAPL|OPT|C|100": "vol", "AAPL|OPT|P|100": "vol"},
    )
    groups = snap.grouped("desk")
    assert {g.group_key for g in groups} == {"desk:vol", "desk:unassigned"}


def test_reconcile_report_counts_and_surfaces_breaches() -> None:
    lines = _pf_lines()
    c100 = next(line for line in lines if line.contract_key == "AAPL|OPT|C|100")
    broker = {
        "AAPL|OPT|C|100": BrokerGreeks(contract_key="AAPL|OPT|C|100", delta=c100.greeks.delta),
        "AAPL|OPT|P|100": BrokerGreeks(contract_key="AAPL|OPT|P|100", delta=0.0),
    }
    report = reconcile_report(lines, broker)
    assert report.compared == 2
    assert not report.ok
    assert [b.contract_key for b in report.breaches] == ["AAPL|OPT|P|100"]


def test_reconcile_report_clean_book_is_ok() -> None:
    report = reconcile_report(_pf_lines(), {})
    assert report.ok
    assert report.compared == 0


def test_build_scenario_report_attribution_and_worst_case() -> None:
    config = ScenarioConfig(version="scn-1", spot_shocks=(-0.05, 0.05), vol_shocks=(0.05,))
    grid = scenario_grid(config)
    lines = _pf_lines()
    version = effective_scenario_version(config)
    report = build_scenario_report(lines, grid, scenario_version=version)
    assert len(report.totals) == len(grid)
    cells = scenario_line_pnls(lines, grid)
    assert report.worst_case.scenario.scenario_id != ""
    by_under_total = sum(a.total_pnl for a in report.worst_case_by_underlying)
    assert by_under_total == pytest.approx(report.worst_case.total_pnl, rel=1e-9)
    families = {s.family for s in grid}
    assert {fa.family for fa in report.by_family} == families
    assert report.scenario_version == version
    assert len(cells) == len(grid) * len(lines)


def test_scenario_result_projects_into_frozen_contract() -> None:
    grid = (Scenario("spot_down_5", "spot", -0.05, 0.0, 0.0),)
    cell = scenario_line_pnls(_pf_lines(), grid)[0]
    result = scenario_result(
        cell, valuation_ts=TS, scenario_version="scn-1+abc",
        source_snapshot_ts=TS, provenance=_stamp((cell.line.contract_key,)),
    )
    assert isinstance(result, ScenarioResult)
    assert result.scenario_id == "spot_down_5"
    assert result.spot_shock == -0.05
    assert result.scenario_pnl == cell.full_reprice_pnl
