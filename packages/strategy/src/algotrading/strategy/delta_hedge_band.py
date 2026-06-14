"""The shared delta-hedge-band rule (course req #9, "Delta-hedge en bande").

The course is explicit (§ "Delta-hedge en bande"): an ATM straddle carries |Δ| ≈ 0.5, and you do
**not** re-hedge continuously to pin delta on its target — every re-hedge pays spread and the
bleed dominates. You keep the position while net delta stays **inside a band** around the target
and **re-hedge only on band exit**, sizing the hedge to bring net delta back to the target.

This module is that rule, factored out of any one strategy so S1 (the synthetic short-forward
index leg), S3 (the p.108 gamma scalp, stock hedge) and S4 share **one** band decision rather
than three drifting copies. It is a pure function of a current net delta and a typed
:class:`DeltaHedgeBand` — no clock, no store, no live read — so it composes into the same
"research == backtest == paper == live" spine every :class:`~algotrading.strategy.Strategy`
method already obeys (TARGET §6).

**What is config and what is invariant.** The band's economic input — the **tolerance width**, the
spread-vs-tracking trade-off the course actually tunes — is a config field (ADR 0028: no economic
parameter is a ``.py`` literal). The *target* a given book hedges to (S1 is delta-flat by
construction → target 0) and the hedge instrument's unit-delta convention (``hedge_ratio``) are
the strategy's own structural choices, carried on the same record so the rule stays one call.
"""

from __future__ import annotations

from dataclasses import dataclass


class DeltaHedgeBandError(ValueError):
    """A :class:`DeltaHedgeBand` was given an economically meaningless parameter.

    Carries the offending field and value (a negative half-width, a zero hedge ratio) so the
    config that produced it can be traced, rather than failing later as a silent mis-sized hedge.
    """

    def __init__(self, field: str, value: float, reason: str) -> None:
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(f"DeltaHedgeBand.{field}={value!r}: {reason}")


@dataclass(frozen=True, slots=True)
class DeltaHedgeBand:
    """A delta-hedge band: hold while net delta is within ``half_width`` of ``target``.

    * ``target`` — the net delta the book hedges *to*. S1 is delta-flat by construction, so its
      target is ``0.0``; a strategy that intends a deliberate directional tilt sets it non-zero.
    * ``half_width`` — the economic tolerance (ADR 0028 config). The band is the closed interval
      ``[target - half_width, target + half_width]``; net delta inside it holds, outside it
      re-hedges. ``0.0`` means "re-hedge on any drift off target" (continuous hedging, the
      degenerate the course warns against — permitted, not chosen).
    * ``hedge_ratio`` — units of the hedge instrument that offset **one unit** of net delta. The
      default ``-1.0`` is the delta-neutralising convention: a long-delta breach is closed by
      selling that many delta-units of the hedge (S1 returns the delta to neutralise directly;
      its booker maps that to synthetic-forward units). A strategy whose hedge instrument carries
      a different per-unit delta scales it here.
    """

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
    """The band rule's verdict on a net delta: trade ``hedge_quantity``, or hold if it is zero.

    ``hedge_quantity`` is the signed quantity of the hedge instrument to trade (the spine's
    "zero quantity == no trade" convention, so a hold and a re-hedge are one typed shape).
    ``breached`` records whether the band was exited (so a caller can distinguish "held inside"
    from "the rule chose not to trade"), and ``reason`` is the human-readable audit line.
    """

    hedge_quantity: float
    breached: bool
    reason: str


def decide_delta_hedge(net_delta: float, band: DeltaHedgeBand) -> HedgeInstruction:
    """Decide hold vs re-hedge for ``net_delta`` against ``band`` — the course's band rule.

    Holds (zero quantity) while net delta is within ``band.half_width`` of ``band.target``;
    on band exit, returns the hedge quantity that brings net delta back to the target,
    ``hedge_ratio × (net_delta − target)`` — the *excess* over target scaled by the
    instrument's delta convention. Pure: same ``(net_delta, band)`` → same instruction.
    """
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
