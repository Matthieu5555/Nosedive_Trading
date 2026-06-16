from __future__ import annotations

from dataclasses import dataclass


class DeltaHedgeBandError(ValueError):

    def __init__(self, field: str, value: float, reason: str) -> None:
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(f"DeltaHedgeBand.{field}={value!r}: {reason}")


@dataclass(frozen=True, slots=True)
class DeltaHedgeBand:

    target: float = 0.0
    half_width: float = 0.0
    hedge_ratio: float = -1.0

    def __post_init__(self) -> None:
        if self.half_width < 0:
            raise DeltaHedgeBandError(
                "half_width", self.half_width, "the band half-width must be non-negative"
            )
        if self.hedge_ratio == 0:
            raise DeltaHedgeBandError(
                "hedge_ratio", self.hedge_ratio, "a zero hedge ratio cannot move net delta"
            )


@dataclass(frozen=True, slots=True)
class HedgeInstruction:

    hedge_quantity: float
    breached: bool
    reason: str


def decide_delta_hedge(net_delta: float, band: DeltaHedgeBand) -> HedgeInstruction:
    excess = net_delta - band.target
    if abs(excess) <= band.half_width:
        return HedgeInstruction(
            0.0,
            False,
            f"net delta {net_delta} within {band.half_width} of target {band.target}; holding",
        )
    hedge_quantity = band.hedge_ratio * excess
    return HedgeInstruction(
        hedge_quantity,
        True,
        f"net delta {net_delta} outside band +/-{band.half_width} of target {band.target}; "
        f"hedging {hedge_quantity} to return to target",
    )
