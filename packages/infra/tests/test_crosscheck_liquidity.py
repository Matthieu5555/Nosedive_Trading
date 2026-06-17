"""INDEPENDENT cross-check of the liquidity exit-difficulty screen — commit 341906a.

Adversarial second opinion on ``liquidity.py``. The oracle is the exit-sessions
formula worked out by hand for a position sitting just on either side of the
bound, plus the two abstain/hard-fail edges:

    exit_sessions = |position| / (participation_rate * captured_volume)

A position is ``inexitable`` iff ``exit_sessions > max_exit_sessions`` (strict).
``captured_volume = None`` -> abstain (``unknown_volume``, exit_sessions None).
``captured_volume = 0`` (a real no-trade session) -> hard ``inexitable`` (inf).

Different config from the implementer's (participation 0.25, bound 2.0 sessions)
so the numbers are independent.
"""

from __future__ import annotations

import math

import pytest
from algotrading.infra.risk import (
    STATUS_INEXITABLE,
    STATUS_OK,
    STATUS_UNKNOWN,
    LiquidityConfig,
    position_liquidity,
)

# 25% of the tape, exit must finish within 2 sessions.
_CFG = LiquidityConfig(participation_rate=0.25, max_exit_sessions=2.0)


def test_just_inside_the_bound_is_ok() -> None:
    # capacity/session = 0.25 * 800 = 200 lots.
    # 380 lots -> 380/200 = 1.9 sessions <= 2.0 -> ok (hand-computed).
    line = position_liquidity(
        contract_key="X|OPT|C|1", position_size=380.0, captured_volume=800.0, config=_CFG
    )
    assert line.exit_sessions == pytest.approx(1.9)
    assert line.status == STATUS_OK
    assert not line.inexitable


def test_just_outside_the_bound_is_inexitable() -> None:
    # 420 lots / 200 capacity = 2.1 > 2.0 -> inexitable (the other side of the bound).
    line = position_liquidity(
        contract_key="X|OPT|C|1", position_size=420.0, captured_volume=800.0, config=_CFG
    )
    assert line.exit_sessions == pytest.approx(2.1)
    assert line.status == STATUS_INEXITABLE
    assert line.inexitable


def test_exactly_on_the_bound_is_ok_strict_inequality() -> None:
    # 400 lots / 200 = exactly 2.0; flag is `> max` so equality stays OK.
    line = position_liquidity(
        contract_key="X|OPT|C|1", position_size=400.0, captured_volume=800.0, config=_CFG
    )
    assert line.exit_sessions == pytest.approx(2.0)
    assert line.status == STATUS_OK


def test_short_position_uses_absolute_size() -> None:
    # -420 lots -> |−420|/200 = 2.1 -> inexitable, same as the long side.
    line = position_liquidity(
        contract_key="X|OPT|C|1", position_size=-420.0, captured_volume=800.0, config=_CFG
    )
    assert line.exit_sessions == pytest.approx(2.1)
    assert line.status == STATUS_INEXITABLE


def test_missing_volume_abstains_rather_than_asserting_liquid() -> None:
    # None volume -> unknown_volume, exit_sessions None (no fabricated "ok").
    line = position_liquidity(
        contract_key="X|OPT|C|1", position_size=380.0, captured_volume=None, config=_CFG
    )
    assert line.status == STATUS_UNKNOWN
    assert line.exit_sessions is None
    assert not line.inexitable


def test_zero_captured_volume_is_a_hard_inexitable() -> None:
    # A real session that did not trade: you cannot exit into it -> inf, inexitable.
    line = position_liquidity(
        contract_key="X|OPT|C|1", position_size=1.0, captured_volume=0.0, config=_CFG
    )
    assert line.status == STATUS_INEXITABLE
    assert line.exit_sessions == math.inf


def test_zero_position_is_trivially_ok() -> None:
    line = position_liquidity(
        contract_key="X|OPT|C|1", position_size=0.0, captured_volume=0.0, config=_CFG
    )
    # Nothing to exit -> ok even against a zero-volume session.
    assert line.status == STATUS_OK
    assert line.exit_sessions == pytest.approx(0.0)
