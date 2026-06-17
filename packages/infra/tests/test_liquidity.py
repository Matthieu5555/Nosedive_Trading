from __future__ import annotations

import math

import pytest
from algotrading.infra.risk import (
    LIQUIDITY_VERSION,
    STATUS_INEXITABLE,
    STATUS_OK,
    STATUS_UNKNOWN,
    LiquidityConfig,
    LiquidityError,
    LiquidityScreenInput,
    liquidity_report,
    position_liquidity,
    screen_inputs,
)

# Config: we will be at most 10% of the tape, exit must complete within 1 session.
CFG = LiquidityConfig(participation_rate=0.10, max_exit_sessions=1.0)


def test_position_inside_the_bound_is_ok() -> None:
    # 50 lots, volume 1000, 10% participation => capacity 100 lots/session.
    # exit_sessions = 50 / 100 = 0.5 <= 1 => ok.
    line = position_liquidity(
        contract_key="AAPL|OPT|C|100", position_size=50.0,
        captured_volume=1000.0, config=CFG,
    )
    assert line.liquidity_version == LIQUIDITY_VERSION
    assert line.exit_sessions == pytest.approx(0.5)
    assert line.status == STATUS_OK
    assert not line.inexitable


def test_position_above_the_bound_is_flagged_inexitable() -> None:
    # 250 lots, volume 1000, 10% => capacity 100/session.
    # exit_sessions = 250 / 100 = 2.5 > 1 => inexitable.
    line = position_liquidity(
        contract_key="AAPL|OPT|C|100", position_size=250.0,
        captured_volume=1000.0, config=CFG,
    )
    assert line.exit_sessions == pytest.approx(2.5)
    assert line.status == STATUS_INEXITABLE
    assert line.inexitable


def test_exactly_at_the_bound_is_ok_not_flagged() -> None:
    # 100 lots / capacity 100 = exactly 1 session == max_exit_sessions => ok (boundary inclusive).
    line = position_liquidity(
        contract_key="AAPL|OPT|C|100", position_size=100.0,
        captured_volume=1000.0, config=CFG,
    )
    assert line.exit_sessions == pytest.approx(1.0)
    assert line.status == STATUS_OK


def test_short_position_size_uses_magnitude() -> None:
    # A -250 lot short is just as hard to buy back as a +250 long is to sell.
    line = position_liquidity(
        contract_key="AAPL|OPT|P|100", position_size=-250.0,
        captured_volume=1000.0, config=CFG,
    )
    assert line.exit_sessions == pytest.approx(2.5)
    assert line.status == STATUS_INEXITABLE


def test_missing_captured_volume_is_unknown_not_ok() -> None:
    # No captured volume => the screen abstains rather than asserting liquidity.
    line = position_liquidity(
        contract_key="AAPL|OPT|C|100", position_size=50.0,
        captured_volume=None, config=CFG,
    )
    assert line.status == STATUS_UNKNOWN
    assert line.exit_sessions is None
    assert not line.inexitable


def test_captured_zero_volume_session_is_hard_inexitable() -> None:
    # A real session that traded nothing: you cannot exit at all.
    line = position_liquidity(
        contract_key="AAPL|OPT|C|100", position_size=1.0,
        captured_volume=0.0, config=CFG,
    )
    assert line.status == STATUS_INEXITABLE
    assert line.exit_sessions == math.inf


def test_zero_position_is_trivially_exitable() -> None:
    line = position_liquidity(
        contract_key="AAPL|OPT|C|100", position_size=0.0,
        captured_volume=0.0, config=CFG,
    )
    assert line.status == STATUS_OK
    assert line.exit_sessions == pytest.approx(0.0)


def test_participation_rate_widens_capacity() -> None:
    # 250 lots, volume 1000, 50% participation => capacity 500/session => 0.5 sessions => ok.
    wide = LiquidityConfig(participation_rate=0.50, max_exit_sessions=1.0)
    line = position_liquidity(
        contract_key="AAPL|OPT|C|100", position_size=250.0,
        captured_volume=1000.0, config=wide,
    )
    assert line.exit_sessions == pytest.approx(0.5)
    assert line.status == STATUS_OK


def test_multi_session_bound_allows_larger_positions() -> None:
    # max_exit_sessions=3: 250/100 = 2.5 <= 3 => ok now.
    patient = LiquidityConfig(participation_rate=0.10, max_exit_sessions=3.0)
    line = position_liquidity(
        contract_key="AAPL|OPT|C|100", position_size=250.0,
        captured_volume=1000.0, config=patient,
    )
    assert line.status == STATUS_OK


@pytest.mark.parametrize("bad_rate", [0.0, -0.1, 1.5])
def test_invalid_participation_rate_is_an_error(bad_rate: float) -> None:
    with pytest.raises(LiquidityError):
        LiquidityConfig(participation_rate=bad_rate)


@pytest.mark.parametrize("bad_sessions", [0.0, -1.0, math.inf, math.nan])
def test_invalid_max_exit_sessions_is_an_error(bad_sessions: float) -> None:
    with pytest.raises(LiquidityError):
        LiquidityConfig(max_exit_sessions=bad_sessions)


def test_negative_volume_is_an_error() -> None:
    with pytest.raises(LiquidityError):
        position_liquidity(
            contract_key="AAPL|OPT|C|100", position_size=1.0,
            captured_volume=-5.0, config=CFG,
        )


def test_blank_contract_key_is_an_error() -> None:
    with pytest.raises(LiquidityError):
        position_liquidity(
            contract_key="  ", position_size=1.0, captured_volume=1.0, config=CFG,
        )


def test_report_partitions_into_inexitable_unknown_and_lines() -> None:
    inputs = [
        LiquidityScreenInput("liquid", 50.0, 1000.0),       # ok
        LiquidityScreenInput("toobig", 250.0, 1000.0),      # inexitable
        LiquidityScreenInput("nodata", 50.0, None),         # unknown
        LiquidityScreenInput("dead", 1.0, 0.0),             # inexitable
    ]
    report = liquidity_report(inputs, config=CFG)
    assert report.liquidity_version == LIQUIDITY_VERSION
    assert report.screened == 4
    assert {line.contract_key for line in report.inexitable} == {"toobig", "dead"}
    assert {line.contract_key for line in report.unknown_volume} == {"nodata"}
    assert len(report.lines) == 4


def test_screen_inputs_adapter_builds_rows() -> None:
    rows = screen_inputs([("a", 10.0, 100.0), ("b", 20.0, None)])
    assert rows[0].contract_key == "a"
    assert rows[1].captured_volume is None
    report = liquidity_report(rows, config=CFG)
    assert report.screened == 2
