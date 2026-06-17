from __future__ import annotations

import pytest
from algotrading.infra_ibkr.preclose_readiness import (
    NO_QUOTE_OBSERVATION,
    NOT_AUTHENTICATED,
    READY,
    TWO_SIDED_BELOW_FLOOR,
    evaluate_readiness,
)

_FLOOR = 0.10


def test_ready_when_authed_and_fraction_at_or_above_floor() -> None:
    verdict = evaluate_readiness(
        authenticated=True, two_sided_fraction=0.10, min_two_sided_fraction=_FLOOR
    )
    assert verdict.ready is True
    assert verdict.exit_code == 0
    assert verdict.reasons == (READY,)


def test_not_ready_when_not_authenticated() -> None:
    verdict = evaluate_readiness(
        authenticated=False, two_sided_fraction=0.99, min_two_sided_fraction=_FLOOR
    )
    assert verdict.ready is False
    assert verdict.exit_code == 1
    assert NOT_AUTHENTICATED in verdict.reasons
    assert "NOT authenticated" in verdict.detail


def test_not_ready_when_fraction_below_floor() -> None:
    verdict = evaluate_readiness(
        authenticated=True, two_sided_fraction=0.05, min_two_sided_fraction=_FLOOR
    )
    assert verdict.ready is False
    assert TWO_SIDED_BELOW_FLOOR in verdict.reasons


def test_not_ready_when_no_quote_observation() -> None:
    # None means "could not observe quotes at all" — conservatively not ready,
    # never a fabricated passing fraction.
    verdict = evaluate_readiness(
        authenticated=True, two_sided_fraction=None, min_two_sided_fraction=_FLOOR
    )
    assert verdict.ready is False
    assert NO_QUOTE_OBSERVATION in verdict.reasons


def test_reports_all_failing_conditions_at_once() -> None:
    verdict = evaluate_readiness(
        authenticated=False, two_sided_fraction=0.0, min_two_sided_fraction=_FLOOR
    )
    assert verdict.ready is False
    assert set(verdict.reasons) == {NOT_AUTHENTICATED, TWO_SIDED_BELOW_FLOOR}


@pytest.mark.parametrize(
    ("fraction", "expected_ready"),
    [(0.099, False), (0.10, True), (0.50, True), (0.0, False)],
)
def test_floor_boundary(fraction: float, expected_ready: bool) -> None:
    verdict = evaluate_readiness(
        authenticated=True, two_sided_fraction=fraction, min_two_sided_fraction=_FLOOR
    )
    assert verdict.ready is expected_ready
