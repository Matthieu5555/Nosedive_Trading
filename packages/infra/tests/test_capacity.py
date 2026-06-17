from __future__ import annotations

import pytest
from algotrading.infra.risk import (
    MARGIN_CAPACITY_VERSION,
    MarginCapacityConfig,
    MarginCapacityError,
    ProspectiveLine,
    ShortPutLine,
    forecast_capacity,
    line_capacity_cap,
    line_initial_margin,
)

TOL = 1e-9


def _config(**overrides: object) -> MarginCapacityConfig:
    base: dict[str, object] = {
        "version": MARGIN_CAPACITY_VERSION,
        "investing_working_capital": 1_000_000.0,
    }
    base.update(overrides)
    return MarginCapacityConfig(**base)  # type: ignore[arg-type]


def _index_put(open_contracts: float = 1.0, strike: float = 4000.0) -> ShortPutLine:
    return ShortPutLine(
        contract_key=f"OESX-{strike:.0f}P",
        open_contracts=open_contracts,
        strike=strike,
        multiplier=10.0,
        premium_per_unit=40.0,
    )


def test_line_initial_margin_is_strike_times_multiplier_when_full():
    margin = line_initial_margin(
        strike=4000.0, multiplier=10.0, premium_per_unit=40.0, config=_config()
    )
    assert margin == pytest.approx(40_000.0, abs=TOL)


def test_line_initial_margin_scales_with_fraction():
    margin = line_initial_margin(
        strike=4000.0,
        multiplier=10.0,
        premium_per_unit=40.0,
        config=_config(initial_margin_fraction=0.2),
    )
    assert margin == pytest.approx(8_000.0, abs=TOL)


def test_line_initial_margin_premium_offset():
    margin = line_initial_margin(
        strike=4000.0,
        multiplier=10.0,
        premium_per_unit=40.0,
        config=_config(premium_offsets_margin=True),
    )
    assert margin == pytest.approx(40_000.0 - 400.0, abs=TOL)


def test_consumed_margin_sums_over_open_lines():
    lines = (
        ShortPutLine(contract_key="a", open_contracts=2.0, strike=4000.0, multiplier=10.0),
        ShortPutLine(contract_key="b", open_contracts=3.0, strike=3800.0, multiplier=10.0),
    )
    forecast = forecast_capacity(lines, config=_config())
    expected = 2.0 * 4000.0 * 10.0 + 3.0 * 3800.0 * 10.0
    assert forecast.consumed_margin == pytest.approx(expected, abs=TOL)
    assert forecast.lines_open == pytest.approx(5.0, abs=TOL)


def test_empty_book_full_headroom():
    forecast = forecast_capacity((), config=_config())
    assert forecast.consumed_margin == pytest.approx(0.0, abs=TOL)
    assert forecast.remaining_headroom == pytest.approx(1_000_000.0, abs=TOL)
    assert forecast.lines_open == pytest.approx(0.0, abs=TOL)
    assert forecast.additional_lines == 0
    assert not forecast.over_capacity
    assert not forecast.at_capacity


def test_headroom_one_more_line():
    prospective = ProspectiveLine(strike=4000.0, multiplier=10.0)
    forecast = forecast_capacity(
        (_index_put(open_contracts=24.0),),
        config=_config(),
        next_line=prospective,
    )
    consumed = 24.0 * 40_000.0
    assert forecast.consumed_margin == pytest.approx(consumed, abs=TOL)
    assert forecast.remaining_headroom == pytest.approx(40_000.0, abs=TOL)
    assert forecast.additional_lines == 1
    assert not forecast.at_capacity
    assert not forecast.over_capacity


def test_headroom_exactly_full_is_at_capacity():
    prospective = ProspectiveLine(strike=4000.0, multiplier=10.0)
    forecast = forecast_capacity(
        (_index_put(open_contracts=25.0),),
        config=_config(),
        next_line=prospective,
    )
    assert forecast.remaining_headroom == pytest.approx(0.0, abs=TOL)
    assert forecast.additional_lines == 0
    assert forecast.at_capacity
    assert not forecast.over_capacity


def test_over_capacity_negative_headroom():
    prospective = ProspectiveLine(strike=4000.0, multiplier=10.0)
    forecast = forecast_capacity(
        (_index_put(open_contracts=26.0),),
        config=_config(),
        next_line=prospective,
    )
    assert forecast.remaining_headroom == pytest.approx(-40_000.0, abs=TOL)
    assert forecast.additional_lines == 0
    assert forecast.over_capacity
    assert not forecast.at_capacity


def test_headroom_floor_reserves_capital():
    prospective = ProspectiveLine(strike=4000.0, multiplier=10.0)
    forecast = forecast_capacity(
        (),
        config=_config(investing_working_capital=100_000.0, headroom_floor=20_000.0),
        next_line=prospective,
    )
    assert forecast.remaining_headroom == pytest.approx(80_000.0, abs=TOL)
    assert forecast.additional_lines == 2


def test_line_capacity_cap_derives_the_30_open_line():
    cap = line_capacity_cap(
        config=_config(investing_working_capital=1_200_000.0),
        representative_line=ProspectiveLine(strike=4000.0, multiplier=10.0),
    )
    assert cap == 30


def test_line_capacity_cap_floors_partial_line():
    cap = line_capacity_cap(
        config=_config(investing_working_capital=1_210_000.0),
        representative_line=ProspectiveLine(strike=4000.0, multiplier=10.0),
    )
    assert cap == 30


def test_forecast_carries_config_version():
    forecast = forecast_capacity((), config=_config(version="margin-capacity-9.9.9"))
    assert forecast.version == "margin-capacity-9.9.9"


def test_from_mapping_round_trips():
    config = MarginCapacityConfig.from_mapping(
        {
            "version": "margin-capacity-2.0.0",
            "investing_working_capital": 500_000.0,
            "initial_margin_fraction": 0.15,
            "premium_offsets_margin": True,
            "headroom_floor": 10_000.0,
        }
    )
    assert config.version == "margin-capacity-2.0.0"
    assert config.investing_working_capital == pytest.approx(500_000.0, abs=TOL)
    assert config.initial_margin_fraction == pytest.approx(0.15, abs=TOL)
    assert config.premium_offsets_margin is True
    assert config.headroom_floor == pytest.approx(10_000.0, abs=TOL)


def test_invalid_config_rejected():
    with pytest.raises(MarginCapacityError):
        _config(investing_working_capital=-1.0)
    with pytest.raises(MarginCapacityError):
        _config(initial_margin_fraction=0.0)
    with pytest.raises(MarginCapacityError):
        _config(initial_margin_fraction=1.5)
    with pytest.raises(MarginCapacityError):
        _config(version="  ")


def test_invalid_line_rejected():
    with pytest.raises(MarginCapacityError):
        ShortPutLine(contract_key="a", open_contracts=-1.0, strike=4000.0, multiplier=10.0)
    with pytest.raises(MarginCapacityError):
        ShortPutLine(contract_key="a", open_contracts=1.0, strike=0.0, multiplier=10.0)
    with pytest.raises(MarginCapacityError):
        ShortPutLine(contract_key=" ", open_contracts=1.0, strike=4000.0, multiplier=10.0)


def test_zero_margin_prospective_rejected():
    config = _config(premium_offsets_margin=True)
    with pytest.raises(MarginCapacityError):
        forecast_capacity(
            (),
            config=config,
            next_line=ProspectiveLine(strike=1.0, multiplier=1.0, premium_per_unit=1.0),
        )


def test_premium_offset_changes_capacity():
    plain = line_capacity_cap(
        config=_config(investing_working_capital=400_000.0),
        representative_line=ProspectiveLine(strike=4000.0, multiplier=10.0, premium_per_unit=40.0),
    )
    offset = line_capacity_cap(
        config=_config(investing_working_capital=400_000.0, premium_offsets_margin=True),
        representative_line=ProspectiveLine(strike=4000.0, multiplier=10.0, premium_per_unit=40.0),
    )
    assert plain == 10
    assert offset == 10
