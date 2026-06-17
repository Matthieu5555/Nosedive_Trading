from __future__ import annotations

import math

import pytest
from algotrading.infra.risk import (
    DEFAULT_KILL_SWITCH_THRESHOLDS,
    KILL_SWITCH_THRESHOLDS_VERSION,
    BookRiskState,
    KillAction,
    KillSwitchThresholds,
    KillTrigger,
    kill_decision,
)

THRESHOLDS = KillSwitchThresholds(
    version="kill-switch-test",
    max_drawdown_fraction=0.20,
    vol_regime_ceiling=0.40,
)

STRATEGY = "S2-index-put-line"


def _state(drawdown: float, vol_regime: float) -> BookRiskState:
    return BookRiskState(
        strategy_label=STRATEGY,
        drawdown_fraction=drawdown,
        vol_regime_level=vol_regime,
    )


def test_holds_when_both_inputs_below_thresholds() -> None:
    decision = kill_decision(_state(0.10, 0.25), thresholds=THRESHOLDS)
    assert decision.action is KillAction.HOLD
    assert decision.flatten is False
    assert decision.triggers == ()
    assert decision.strategy_label == STRATEGY
    assert decision.threshold_version == "kill-switch-test"


@pytest.mark.parametrize(
    ("drawdown", "expected_action", "expected_triggers"),
    [
        pytest.param(0.19999, KillAction.HOLD, (), id="just-below-drawdown-max"),
        pytest.param(0.20, KillAction.FLATTEN, (KillTrigger.DRAWDOWN,), id="at-drawdown-max"),
        pytest.param(
            0.20001, KillAction.FLATTEN, (KillTrigger.DRAWDOWN,), id="just-above-drawdown-max"
        ),
    ],
)
def test_drawdown_trigger_boundary(
    drawdown: float,
    expected_action: KillAction,
    expected_triggers: tuple[KillTrigger, ...],
) -> None:
    decision = kill_decision(_state(drawdown, 0.10), thresholds=THRESHOLDS)
    assert decision.action is expected_action
    assert decision.triggers == expected_triggers


@pytest.mark.parametrize(
    ("vol_regime", "expected_action", "expected_triggers"),
    [
        pytest.param(0.39999, KillAction.HOLD, (), id="just-below-vol-ceiling"),
        pytest.param(0.40, KillAction.FLATTEN, (KillTrigger.VOL_REGIME,), id="at-vol-ceiling"),
        pytest.param(
            0.40001, KillAction.FLATTEN, (KillTrigger.VOL_REGIME,), id="just-above-vol-ceiling"
        ),
    ],
)
def test_vol_regime_trigger_boundary(
    vol_regime: float,
    expected_action: KillAction,
    expected_triggers: tuple[KillTrigger, ...],
) -> None:
    decision = kill_decision(_state(0.05, vol_regime), thresholds=THRESHOLDS)
    assert decision.action is expected_action
    assert decision.triggers == expected_triggers


def test_both_triggers_fire_together() -> None:
    decision = kill_decision(_state(0.30, 0.55), thresholds=THRESHOLDS)
    assert decision.action is KillAction.FLATTEN
    assert decision.triggers == (KillTrigger.DRAWDOWN, KillTrigger.VOL_REGIME)


@pytest.mark.parametrize(
    "drawdown",
    [
        pytest.param(float("nan"), id="nan-drawdown"),
        pytest.param(float("inf"), id="inf-drawdown"),
        pytest.param(float("-inf"), id="neg-inf-drawdown"),
    ],
)
def test_non_finite_drawdown_flattens_fail_safe(drawdown: float) -> None:
    decision = kill_decision(_state(drawdown, 0.10), thresholds=THRESHOLDS)
    assert decision.action is KillAction.FLATTEN
    assert KillTrigger.NON_FINITE_DRAWDOWN in decision.triggers


@pytest.mark.parametrize(
    "vol_regime",
    [
        pytest.param(float("nan"), id="nan-vol"),
        pytest.param(float("inf"), id="inf-vol"),
    ],
)
def test_non_finite_vol_regime_flattens_fail_safe(vol_regime: float) -> None:
    decision = kill_decision(_state(0.05, vol_regime), thresholds=THRESHOLDS)
    assert decision.action is KillAction.FLATTEN
    assert KillTrigger.NON_FINITE_VOL_REGIME in decision.triggers


def test_default_thresholds_are_versioned() -> None:
    assert DEFAULT_KILL_SWITCH_THRESHOLDS.version == KILL_SWITCH_THRESHOLDS_VERSION
    assert DEFAULT_KILL_SWITCH_THRESHOLDS.max_drawdown_fraction == pytest.approx(0.20)
    assert DEFAULT_KILL_SWITCH_THRESHOLDS.vol_regime_ceiling == pytest.approx(0.40)


def test_default_thresholds_hold_a_quiet_book() -> None:
    decision = kill_decision(_state(0.05, 0.18))
    assert decision.action is KillAction.HOLD


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("max_drawdown_fraction", 0.0, id="drawdown-zero"),
        pytest.param("max_drawdown_fraction", -0.1, id="drawdown-negative"),
        pytest.param("max_drawdown_fraction", 1.5, id="drawdown-above-one"),
        pytest.param("vol_regime_ceiling", 0.0, id="vol-ceiling-zero"),
        pytest.param("vol_regime_ceiling", -0.2, id="vol-ceiling-negative"),
    ],
)
def test_thresholds_reject_invalid_values(field: str, value: float) -> None:
    kwargs = {
        "version": "kill-switch-test",
        "max_drawdown_fraction": 0.20,
        "vol_regime_ceiling": 0.40,
    }
    kwargs[field] = value
    with pytest.raises(ValueError):
        KillSwitchThresholds(**kwargs)


def test_thresholds_reject_empty_version() -> None:
    with pytest.raises(ValueError):
        KillSwitchThresholds(
            version="", max_drawdown_fraction=0.20, vol_regime_ceiling=0.40
        )


def test_from_mapping_hydrates_thresholds() -> None:
    thresholds = KillSwitchThresholds.from_mapping(
        {
            "version": "kill-switch-yaml",
            "max_drawdown_fraction": 0.15,
            "vol_regime_ceiling": 0.50,
        }
    )
    assert thresholds.version == "kill-switch-yaml"
    assert thresholds.max_drawdown_fraction == pytest.approx(0.15)
    assert thresholds.vol_regime_ceiling == pytest.approx(0.50)
    decision = kill_decision(_state(0.16, 0.10), thresholds=thresholds)
    assert decision.action is KillAction.FLATTEN
    assert decision.triggers == (KillTrigger.DRAWDOWN,)


def test_from_mapping_defaults_version() -> None:
    thresholds = KillSwitchThresholds.from_mapping(
        {"max_drawdown_fraction": 0.25, "vol_regime_ceiling": 0.60}
    )
    assert thresholds.version == KILL_SWITCH_THRESHOLDS_VERSION


def test_decision_is_pure_and_repeatable() -> None:
    state = _state(0.22, 0.10)
    first = kill_decision(state, thresholds=THRESHOLDS)
    second = kill_decision(state, thresholds=THRESHOLDS)
    assert first == second
    assert math.isfinite(state.drawdown_fraction)
