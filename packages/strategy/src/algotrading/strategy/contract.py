"""The typed ``StrategyContract`` — the four §3 columns as inspectable, testable data.

TARGET §1: "a strategy here is a contract: it names the premium it harvests, the signal
that triggers it, the Greeks it intends to hold, and its kill condition." TARGET §3 lays
those four columns out as the strategy-book table. This module turns that table row into a
**frozen, typed record** — one per strategy — so the contract is data the system can read,
display, and *check P&L against* (attribution enforces "P&L lands in the intended Greeks,
residual elsewhere"), never just prose in a docstring.

This is the spine, not any one strategy: S1–S5 each declare *their* ``StrategyContract``
instance; nothing here encodes a specific strategy's premium or rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class StrategyContractError(ValueError):
    """A ``StrategyContract`` field was malformed, carrying the offending value.

    Mirrors the infra ``ContractValidationError`` shape (field + value + reason) so a
    rejected strategy declaration says exactly which column was wrong, not just "invalid".
    """

    def __init__(self, field: str, value: object, reason: str) -> None:
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(f"StrategyContract.{field}={value!r}: {reason}")


class GreekSign(StrEnum):
    """The intended sign of one Greek exposure in a strategy's :class:`IntendedGreeks`.

    ``intended_greeks`` is what attribution checks realized P&L against (TARGET §5.2:
    "P&L must land in the intended Greeks, residual elsewhere"). The signed *direction* of
    each exposure — not a magnitude, which is a sizing/construction concern the S-tasks own —
    is the testable part of the contract: a dispersion book intends ``LONG`` gamma/vega and
    ``FLAT`` net delta, and an attribution that shows the delta term carrying the P&L is a
    contract breach the grouping can flag.
    """

    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class SignalKind(StrEnum):
    """The named signal family that triggers a strategy's entry (TARGET §3 / §5.x).

    These are the entry triggers the §3 book uses, each computed by the infra signal layer
    (``infra-signal-layer``, not built here — the strategy *reads* the value, it does not
    compute it):

    * ``IMPLIED_CORRELATION`` — average implied correlation ρ̄ from the basket identity (S1).
    * ``IV_VS_REALIZED`` — implied vol rich/cheap vs realized (S2, S3).
    * ``IV_RANK`` — IV rank / percentile per name from banked history (S3).
    * ``TERM_STRUCTURE_SLOPE`` — front-vs-back vol term slope (S5).
    * ``RANGE_PREMIUM`` — range/strangle premium on a held name (S4).
    """

    IMPLIED_CORRELATION = "implied_correlation"
    IV_VS_REALIZED = "iv_vs_realized"
    IV_RANK = "iv_rank"
    TERM_STRUCTURE_SLOPE = "term_structure_slope"
    RANGE_PREMIUM = "range_premium"


@dataclass(frozen=True, slots=True)
class IntendedGreeks:
    """The Greek profile a strategy's position is *supposed* to hold (TARGET §3 col 3).

    A signed-direction declaration per Greek — the thing attribution checks realized P&L
    against, not a sizing target. Charm/vanna/volga are not declared: the four first-order
    book Greeks are the contract the §3 table names ("long single-name gamma/vega, ~0 net
    delta"); higher-order terms live in the residual the contract does not promise.
    """

    delta: GreekSign
    gamma: GreekSign
    vega: GreekSign
    theta: GreekSign


@dataclass(frozen=True, slots=True)
class StrategyContract:
    """One strategy's §1/§3 contract: premium / signal / intended Greeks / kill condition.

    Frozen, typed, one instance per strategy. The four columns of the §3 book table:

    * ``premium_harvested`` — the named premium the strategy monetizes (free text, the §3
      "Premium harvested" column, e.g. "correlation premium: index IV rich vs constituent IVs").
    * ``signal`` — the :class:`SignalKind` that triggers entry (the §3 entry trigger).
    * ``intended_greeks`` — the :class:`IntendedGreeks` the position is meant to hold.
    * ``kill_condition`` — the declared death mode (the §3 "Dies when" column), free text;
      the *decision* that fires it is :meth:`~algotrading.strategy.Strategy.decide_exit`,
      and the kill switch in execution *enforces* it (a cross-lane seam, not here).

    ``premium_harvested`` / ``kill_condition`` are intentionally free text: they are the
    human-named economic thesis, not a machine rule (the rule is the signal + the decisions).
    Both must be non-empty — a contract with no named premium or no declared death mode is
    malformed, rejected with the offending value.
    """

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
