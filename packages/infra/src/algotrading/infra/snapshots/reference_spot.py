from __future__ import annotations

from dataclasses import dataclass

REFERENCE_TYPES = ("mid", "last", "close", "carry_forward")


class NoReferenceSpot(Exception):

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class ReferenceSpot:

    value: float
    reference_type: str
    bid: float
    ask: float
    last: float
    spread_pct: float
    is_fallback: bool


def is_valid_two_sided(bid: float | None, ask: float | None) -> bool:
    return bid is not None and ask is not None and bid > 0.0 and ask > 0.0 and bid <= ask


def resolve_reference_spot(
    *,
    bid: float | None,
    ask: float | None,
    last: float | None,
    prior_close: float | None = None,
    prior_spot: float | None = None,
) -> ReferenceSpot:
    observed_bid = bid if (bid is not None and bid > 0.0) else 0.0
    observed_ask = ask if (ask is not None and ask > 0.0) else 0.0
    observed_last = last if (last is not None and last > 0.0) else 0.0

    if is_valid_two_sided(bid, ask):
        assert bid is not None and ask is not None
        mid = 0.5 * (bid + ask)
        spread_pct = (ask - bid) / mid if mid > 0.0 else 0.0
        return ReferenceSpot(mid, "mid", bid, ask, observed_last, spread_pct, False)

    if last is not None and last > 0.0:
        return ReferenceSpot(last, "last", observed_bid, observed_ask, last, 0.0, True)

    if prior_close is not None and prior_close > 0.0:
        return ReferenceSpot(
            prior_close, "close", observed_bid, observed_ask, observed_last, 0.0, True
        )

    if prior_spot is not None and prior_spot > 0.0:
        return ReferenceSpot(
            prior_spot, "carry_forward", observed_bid, observed_ask, observed_last, 0.0, True
        )

    raise NoReferenceSpot(
        "no valid two-sided quote, last trade, prior close, or carry-forward spot"
    )
