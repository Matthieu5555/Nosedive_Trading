from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class StrategyContractError(ValueError):

    def __init__(self, field: str, value: object, reason: str) -> None:
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(f"StrategyContract.{field}={value!r}: {reason}")


class GreekSign(StrEnum):

    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class SignalKind(StrEnum):

    IMPLIED_CORRELATION = "implied_correlation"
    IV_VS_REALIZED = "iv_vs_realized"
    IV_RANK = "iv_rank"
    TERM_STRUCTURE_SLOPE = "term_structure_slope"
    RANGE_PREMIUM = "range_premium"


@dataclass(frozen=True, slots=True)
class IntendedGreeks:

    delta: GreekSign
    gamma: GreekSign
    vega: GreekSign
    theta: GreekSign


@dataclass(frozen=True, slots=True)
class StrategyContract:

    strategy_id: str
    premium_harvested: str
    signal: SignalKind
    intended_greeks: IntendedGreeks
    kill_condition: str

    def __post_init__(self) -> None:
        if not self.strategy_id.strip():
            raise StrategyContractError(
                "strategy_id", self.strategy_id, "must be a non-empty identity stamp"
            )
        if not self.premium_harvested.strip():
            raise StrategyContractError(
                "premium_harvested", self.premium_harvested, "must name the harvested premium"
            )
        if not self.kill_condition.strip():
            raise StrategyContractError(
                "kill_condition", self.kill_condition, "must declare the death mode"
            )
