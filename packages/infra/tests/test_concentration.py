from __future__ import annotations

import pytest
from algotrading.infra.risk import (
    CONCENTRATION_VERSION,
    AggregationError,
    ConcentrationError,
    PositionRisk,
    concentration_metric,
    concentration_report,
    position_risk,
)
from fixtures.positions import RISK_VALUATIONS, risk_positions


def pf_lines() -> list[PositionRisk]:
    return [
        position_risk(
            portfolio_id="pf-risk",
            quantity=p.quantity,
            valuation=RISK_VALUATIONS[p.contract_key],
        )
        for p in risk_positions()
    ]


def test_single_bucket_book_is_maximally_concentrated() -> None:
    # One contract only: all exposure sits in one underlying/maturity/instrument
    # bucket, so HHI = 1.0 and top_share = 1.0 on every greek.
    line = position_risk(
        portfolio_id="pf-risk",
        quantity=10.0,
        valuation=RISK_VALUATIONS["AAPL|OPT|C|100"],
    )
    metric = concentration_metric(
        [line], portfolio_id="pf-risk", dimension="instrument", greek="delta"
    )
    assert metric.concentration_version == CONCENTRATION_VERSION
    assert metric.bucket_count == 1
    assert metric.herfindahl == pytest.approx(1.0)
    assert metric.top_share == pytest.approx(1.0)
    assert metric.top_group_key == "instrument:AAPL|OPT|C|100"


def test_two_equal_buckets_give_hhi_one_half() -> None:
    # Two contracts with equal-magnitude vega exposure split 50/50 across the
    # instrument axis: shares (0.5, 0.5) => HHI = 0.5^2 + 0.5^2 = 0.5, top = 0.5.
    # Hand-derived: build two lines whose position_vega is equal in magnitude.
    long_call = position_risk(
        portfolio_id="pf-risk", quantity=4.0,
        valuation=RISK_VALUATIONS["AAPL|OPT|C|100"],
    )
    # same contract template but a different key/strike so they bucket separately,
    # equal quantity => equal vega magnitude (puts and calls share vega at same strike).
    put_same = position_risk(
        portfolio_id="pf-risk", quantity=4.0,
        valuation=RISK_VALUATIONS["AAPL|OPT|P|100"],
    )
    metric = concentration_metric(
        [long_call, put_same], portfolio_id="pf-risk",
        dimension="instrument", greek="vega",
    )
    assert metric.bucket_count == 2
    assert metric.herfindahl == pytest.approx(0.5)
    assert metric.top_share == pytest.approx(0.5)


def test_underlying_axis_collapses_same_name_into_one_bucket() -> None:
    # All three fixture positions are AAPL => one underlying bucket => HHI 1.0.
    metric = concentration_metric(
        pf_lines(), portfolio_id="pf-risk", dimension="underlying", greek="delta"
    )
    assert metric.bucket_count == 1
    assert metric.top_group_key == "underlying:AAPL"
    assert metric.herfindahl == pytest.approx(1.0)


def test_shares_sum_to_one_and_are_absolute() -> None:
    metric = concentration_metric(
        pf_lines(), portfolio_id="pf-risk", dimension="instrument", greek="delta"
    )
    assert sum(s.abs_share for s in metric.shares) == pytest.approx(1.0)
    assert all(s.abs_share >= 0.0 for s in metric.shares)


def test_long_and_short_do_not_cancel_in_concentration() -> None:
    # A +10 and a -10 delta in two buckets is HIGHLY concentrated risk, not flat.
    # Absolute shares => HHI 0.5, not a divide-by-zero on net 0.
    long_line = position_risk(
        portfolio_id="pf-risk", quantity=5.0,
        valuation=RISK_VALUATIONS["AAPL|OPT|C|100"],
    )
    short_line = position_risk(
        portfolio_id="pf-risk", quantity=-5.0,
        valuation=RISK_VALUATIONS["AAPL|OPT|C|105"],
    )
    metric = concentration_metric(
        [long_line, short_line], portfolio_id="pf-risk",
        dimension="instrument", greek="delta",
    )
    assert metric.total_abs_exposure > 0.0
    assert metric.bucket_count == 2


def test_zero_net_greek_reports_undefined_shares_not_a_crash() -> None:
    # theta exactly cancels? Use a contrived all-zero case: a single long/short of the
    # same contract nets to a zero-quantity line, so every greek exposure is 0.
    a = position_risk(
        portfolio_id="pf-risk", quantity=5.0,
        valuation=RISK_VALUATIONS["AAPL|OPT|C|100"],
    )
    b = position_risk(
        portfolio_id="pf-risk", quantity=-5.0,
        valuation=RISK_VALUATIONS["AAPL|OPT|C|100"],
    )
    metric = concentration_metric(
        [a, b], portfolio_id="pf-risk", dimension="instrument", greek="delta"
    )
    assert metric.total_abs_exposure == pytest.approx(0.0)
    assert metric.herfindahl == 0.0
    assert metric.top_share == 0.0


def test_unknown_greek_is_an_error() -> None:
    with pytest.raises(ConcentrationError):
        concentration_metric(
            pf_lines(), portfolio_id="pf-risk", dimension="instrument", greek="rho"
        )


def test_unknown_dimension_is_an_error() -> None:
    with pytest.raises(AggregationError):
        concentration_metric(
            pf_lines(), portfolio_id="pf-risk", dimension="sector", greek="delta"
        )


def test_report_covers_every_axis_greek_pair() -> None:
    report = concentration_report(
        pf_lines(),
        portfolio_id="pf-risk",
        dimensions=("instrument", "underlying"),
        greeks=("delta", "vega"),
    )
    assert report.portfolio_id == "pf-risk"
    assert len(report.metrics) == 4
    pairs = {(m.dimension, m.greek) for m in report.metrics}
    assert pairs == {
        ("instrument", "delta"),
        ("instrument", "vega"),
        ("underlying", "delta"),
        ("underlying", "vega"),
    }


def test_report_requires_at_least_one_dimension_and_greek() -> None:
    with pytest.raises(ConcentrationError):
        concentration_report(pf_lines(), portfolio_id="pf-risk", dimensions=())
    with pytest.raises(ConcentrationError):
        concentration_report(pf_lines(), portfolio_id="pf-risk", greeks=())


def test_hhi_is_bounded_between_one_over_n_and_one() -> None:
    # Three distinct instrument buckets => HHI lies in [1/3, 1].
    lines = [
        position_risk(portfolio_id="pf-risk", quantity=4.0,
                      valuation=RISK_VALUATIONS["AAPL|OPT|C|100"]),
        position_risk(portfolio_id="pf-risk", quantity=4.0,
                      valuation=RISK_VALUATIONS["AAPL|OPT|P|100"]),
        position_risk(portfolio_id="pf-risk", quantity=4.0,
                      valuation=RISK_VALUATIONS["AAPL|OPT|C|105"]),
    ]
    metric = concentration_metric(
        lines, portfolio_id="pf-risk", dimension="instrument", greek="vega"
    )
    assert metric.bucket_count == 3
    # vega differs by strike, so we only assert the HHI is bounded in [1/3, 1].
    assert 1.0 / 3.0 - 1e-9 <= metric.herfindahl <= 1.0 + 1e-9
