"""INDEPENDENT cross-check of concentration (HHI + top-share) — commit 341906a.

The implementer's ``test_concentration.py`` drives the metric through the priced
``risk_positions()`` fixture (greeks come out of the BS engine). This cross-check
deliberately bypasses that engine: it builds ``PositionRisk`` rows with
HAND-SET ``PriceGreeks`` and ``multiplier = quantity = 1`` so the net exposure on
each bucket equals the chosen greek EXACTLY. That makes the Herfindahl index and
the top share hand-computable from first principles, independent of any fixture.

Oracle (3-bucket case): net |delta| exposures 6, 3, 1 over three distinct
instruments. total_abs = 10. shares = 0.6, 0.3, 0.1.
    HHI = 0.6^2 + 0.3^2 + 0.1^2 = 0.36 + 0.09 + 0.01 = 0.46
    top_share = 0.6
Absolute value matters: a +6 and a -3 must NOT cancel — they are concentration on
opposite sides, so the share is built on |net|, giving the same 0.46.
"""

from __future__ import annotations

import pytest
from algotrading.infra.pricing.state import PriceGreeks
from algotrading.infra.risk import ConcentrationError, concentration_metric
from algotrading.infra.risk.greeks import PositionRisk
from algotrading.infra.risk.valuation import ContractValuationInput

_PF = "pf-xcheck"


def _valuation(contract_key: str) -> ContractValuationInput:
    # multiplier 1.0 so position_<greek> = greek * quantity exactly.
    return ContractValuationInput(
        contract_key=contract_key,
        underlying="ZZZ",
        option_right="C",
        exercise_style="european",
        strike=100.0,
        maturity_years=0.25,
        spot=100.0,
        carry=0.0,
        volatility=0.2,
        discount_factor=1.0,
        multiplier=1.0,
        currency="USD",
    )


def _line(contract_key: str, *, delta: float) -> PositionRisk:
    # quantity 1, multiplier 1 -> position_delta == delta exactly.
    greeks = PriceGreeks(price=1.0, delta=delta, gamma=0.0, vega=0.0, theta=0.0, rho=0.0)
    return PositionRisk(
        portfolio_id=_PF,
        quantity=1.0,
        valuation=_valuation(contract_key),
        greeks=greeks,
    )


def test_three_bucket_hhi_and_top_share_hand_computed() -> None:
    # net deltas 6, 3, 1 across three distinct instruments (so three buckets).
    lines = [
        _line("A", delta=6.0),
        _line("B", delta=3.0),
        _line("C", delta=1.0),
    ]
    metric = concentration_metric(
        lines, portfolio_id=_PF, dimension="instrument", greek="delta"
    )
    # Hand-computed: total_abs = 10, HHI = 0.46, top_share = 0.6.
    assert metric.total_abs_exposure == pytest.approx(10.0)
    assert metric.bucket_count == 3
    assert metric.herfindahl == pytest.approx(0.46)
    assert metric.top_share == pytest.approx(0.6)
    assert metric.top_group_key == "instrument:A"


def test_opposite_signs_do_not_cancel_absolute_share() -> None:
    # +6 and -3 and +1 -> |net| 6, 3, 1 -> identical HHI 0.46 (no netting).
    lines = [
        _line("A", delta=6.0),
        _line("B", delta=-3.0),
        _line("C", delta=1.0),
    ]
    metric = concentration_metric(
        lines, portfolio_id=_PF, dimension="instrument", greek="delta"
    )
    assert metric.total_abs_exposure == pytest.approx(10.0)
    assert metric.herfindahl == pytest.approx(0.46)
    assert metric.top_share == pytest.approx(0.6)


def test_n_even_buckets_give_hhi_one_over_n() -> None:
    # Four equal |delta| buckets -> shares all 0.25 -> HHI = 4 * 0.25^2 = 0.25 = 1/n.
    lines = [_line(k, delta=5.0) for k in ("A", "B", "C", "D")]
    metric = concentration_metric(
        lines, portfolio_id=_PF, dimension="instrument", greek="delta"
    )
    assert metric.herfindahl == pytest.approx(0.25)  # 1/4
    assert metric.top_share == pytest.approx(0.25)


def test_zero_net_exposure_book_reports_no_concentration_not_a_div_by_zero() -> None:
    # delta exactly zero on every bucket -> total_abs = 0 -> honest 0.0, not 0/0.
    lines = [_line("A", delta=0.0), _line("B", delta=0.0)]
    metric = concentration_metric(
        lines, portfolio_id=_PF, dimension="instrument", greek="delta"
    )
    assert metric.total_abs_exposure == 0.0
    assert metric.herfindahl == 0.0
    assert metric.top_share == 0.0
    assert metric.top_group_key == ""


def test_unknown_greek_is_rejected() -> None:
    with pytest.raises(ConcentrationError):
        concentration_metric(
            [_line("A", delta=1.0)],
            portfolio_id=_PF,
            dimension="instrument",
            greek="charm",  # not one of delta/gamma/vega/theta
        )
