"""INDEPENDENT cross-check of the tail-risk metrics (commit 341906a).

This is an adversarial second opinion on ``tail_risk.py`` — NOT a rewrite of the
implementer's ``test_tail_risk.py``. The oracles here are derived from a different
angle:

* VaR/ES are recomputed from a tiny P&L vector by sorting the losses *by hand* in
  the docstring and asserting the function reproduces the hand-sorted value.
* ``_tail_count`` is probed at the rounding boundary the implementer's
  20-observation vector never exercises: a sample size where ``(1-c)*n`` lands on
  exactly 0.5 (Python ``round`` is banker's-rounding → 0) and the ``ceil`` fallback
  must rescue it to 1; and the snap-tolerance case where ``(1-c)*n`` is an integer
  to floating-point fuzz.

No fixtures, no shared P&L vector with the implementer — every input is local and
the expected value is worked out in the comment above the assertion.
"""

from __future__ import annotations

import pytest
from algotrading.infra.risk.tail_risk import (
    _tail_count,
    expected_shortfall,
    value_at_risk,
)

# --------------------------------------------------------------------------- #
# VaR / ES from a hand-sorted loss vector
# --------------------------------------------------------------------------- #


def test_var_es_95_on_hand_sorted_forty_observation_vector() -> None:
    # 40 observations; losses = -pnl. Pick pnls so the loss ordering is obvious.
    # pnls = +1..+36 (36 gains) then the four worst days: -50, -80, -120, -300.
    # losses sorted descending: 300, 120, 80, 50, then 36 negatives (gains -> -1..-36).
    # tail_count(40, 0.95): (1-0.95)*40 = 2.0 -> round = 2.
    # VaR(0.95) = the 2nd-worst loss = 120.0  (hand-read from the sorted list).
    # ES(0.95)  = mean of the top 2 losses = (300 + 120)/2 = 210.0.
    gains = [float(x) for x in range(1, 37)]  # +1 .. +36
    tail = [-50.0, -80.0, -120.0, -300.0]
    pnls = gains + tail
    assert len(pnls) == 40

    assert value_at_risk(pnls, 0.95) == pytest.approx(120.0)
    assert expected_shortfall(pnls, 0.95) == pytest.approx(210.0)


def test_es_equals_mean_of_the_explicit_tail_losses() -> None:
    # Five losses, no gains, conf 0.60 -> (1-0.6)*5 = 2.0 -> count 2.
    # losses desc: 9, 7, 5, 3, 1.  VaR = 2nd = 7.  ES = (9+7)/2 = 8.
    pnls = (-1.0, -3.0, -5.0, -7.0, -9.0)
    assert value_at_risk(pnls, 0.60) == pytest.approx(7.0)
    assert expected_shortfall(pnls, 0.60) == pytest.approx(8.0)


# --------------------------------------------------------------------------- #
# _tail_count boundary — the non-trivial rounding the 20-vector never hits
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("n", "confidence", "expected", "why"),
    [
        # (1-0.95)*10 = 0.5 -> Python round(0.5)=0 (banker's), snap |0.5-0|>1e-9,
        # so falls to ceil(0.5)=1, then max(.,1)=1. The half-integer trap.
        (10, 0.95, 1, "half-integer rounds DOWN via banker's, ceil rescues to 1"),
        # (1-0.95)*30 = 1.5 -> round(1.5)=2 (banker's rounds to even), |1.5-2|=0.5,
        # ceil(1.5)=2 -> 2. Both paths agree here.
        (30, 0.95, 2, "1.5 -> 2"),
        # (1-0.95)*50 = 2.5 -> round(2.5)=2 (banker's to even), |2.5-2|=0.5 -> ceil=3.
        (50, 0.95, 3, "2.5 banker's-down to 2, ceil rescues to 3"),
        # (1-0.90)*40 = 4.0 exactly -> round=4, snapped within tol -> 4.
        (40, 0.90, 4, "integer-valued raw stays as-is"),
        # tiny sample, min-1 floor: (1-0.99)*1 = 0.01 -> ceil 1 -> max 1.
        (1, 0.99, 1, "min-one floor on a single observation"),
    ],
)
def test_tail_count_boundary(n: int, confidence: float, expected: int, why: str) -> None:
    assert _tail_count(n, confidence) == expected, why


def test_tail_count_snap_tolerance_treats_near_integer_as_integer() -> None:
    # An exactly-representable product that is an integer must NOT be ceil-bumped.
    # (1-0.95)*20 = 1.0 -> 1, never 2.  (Cross-checks the snap path, not ceil.)
    assert _tail_count(20, 0.95) == 1
    # And the value VaR returns at that count on a known vector:
    # losses desc for pnls -10..-200 stepping: choose the single worst loss.
    pnls = tuple(-float(x) for x in (10, 20, 50, 200) * 5)  # 20 obs, worst loss 200
    assert value_at_risk(pnls, 0.95) == pytest.approx(200.0)
