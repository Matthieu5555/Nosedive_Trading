from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from algotrading.core.log import get_logger
from pydantic import BaseModel, ConfigDict, Field

KILL_SWITCH_THRESHOLDS_VERSION = "kill-switch-1.0.0"

_log = get_logger(__name__)


class KillAction(StrEnum):

    FLATTEN = "flatten"
    HOLD = "hold"


class KillTrigger(StrEnum):

    DRAWDOWN = "drawdown"
    VOL_REGIME = "vol_regime"
    NON_FINITE_DRAWDOWN = "non_finite_drawdown"
    NON_FINITE_VOL_REGIME = "non_finite_vol_regime"


class KillSwitchThresholds(BaseModel):

    model_config = ConfigDict(frozen=True, extra="forbid", strict=False)

    version: str = Field(min_length=1)
    max_drawdown_fraction: float = Field(gt=0.0, le=1.0)
    vol_regime_ceiling: float = Field(gt=0.0)

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> KillSwitchThresholds:
        return cls(
            version=str(section.get("version", KILL_SWITCH_THRESHOLDS_VERSION)),
            max_drawdown_fraction=float(section["max_drawdown_fraction"]),
            vol_regime_ceiling=float(section["vol_regime_ceiling"]),
        )


DEFAULT_KILL_SWITCH_THRESHOLDS = KillSwitchThresholds(
    version=KILL_SWITCH_THRESHOLDS_VERSION,
    max_drawdown_fraction=0.20,
    vol_regime_ceiling=0.40,
)


@dataclass(frozen=True, slots=True)
class BookRiskState:

    strategy_label: str
    drawdown_fraction: float
    vol_regime_level: float


@dataclass(frozen=True, slots=True)
class KillDecision:

    action: KillAction
    strategy_label: str
    triggers: tuple[KillTrigger, ...]
    reason: str
    threshold_version: str

    @property
    def flatten(self) -> bool:
        return self.action is KillAction.FLATTEN


def kill_decision(
    state: BookRiskState,
    *,
    thresholds: KillSwitchThresholds = DEFAULT_KILL_SWITCH_THRESHOLDS,
) -> KillDecision:
    triggers: list[KillTrigger] = []
    reasons: list[str] = []

    if not math.isfinite(state.drawdown_fraction):
        triggers.append(KillTrigger.NON_FINITE_DRAWDOWN)
        reasons.append(
            f"drawdown_fraction is non-finite ({state.drawdown_fraction}); "
            f"flattening fail-safe rather than trusting a corrupt P&L"
        )
    elif state.drawdown_fraction >= thresholds.max_drawdown_fraction:
        triggers.append(KillTrigger.DRAWDOWN)
        reasons.append(
            f"drawdown {state.drawdown_fraction} at/above max "
            f"{thresholds.max_drawdown_fraction}: the short left tail is hitting"
        )

    if not math.isfinite(state.vol_regime_level):
        triggers.append(KillTrigger.NON_FINITE_VOL_REGIME)
        reasons.append(
            f"vol_regime_level is non-finite ({state.vol_regime_level}); "
            f"flattening fail-safe rather than trusting a corrupt vol reading"
        )
    elif state.vol_regime_level >= thresholds.vol_regime_ceiling:
        triggers.append(KillTrigger.VOL_REGIME)
        reasons.append(
            f"vol regime {state.vol_regime_level} at/above ceiling "
            f"{thresholds.vol_regime_ceiling}: a vol-regime spike compounds the tail"
        )

    if triggers:
        _log.warning(
            "kill switch flatten",
            extra={
                "strategy_label": state.strategy_label,
                "triggers": [trigger.value for trigger in triggers],
                "drawdown_fraction": state.drawdown_fraction,
                "vol_regime_level": state.vol_regime_level,
                "threshold_version": thresholds.version,
            },
        )
        return KillDecision(
            action=KillAction.FLATTEN,
            strategy_label=state.strategy_label,
            triggers=tuple(triggers),
            reason="; ".join(reasons),
            threshold_version=thresholds.version,
        )

    return KillDecision(
        action=KillAction.HOLD,
        strategy_label=state.strategy_label,
        triggers=(),
        reason=(
            f"drawdown {state.drawdown_fraction} below max "
            f"{thresholds.max_drawdown_fraction} and vol regime {state.vol_regime_level} "
            f"below ceiling {thresholds.vol_regime_ceiling}; holding the line"
        ),
        threshold_version=thresholds.version,
    )
