"""Reference spot via mid, with documented, labeled fallbacks (roadmap Eq 1).

The reference price is chosen by a fixed ladder, each rung firing under its own
condition and each labeled in ``reference_type`` so a consumer always knows which
rung produced the number — there is no hidden fallback:

1. ``mid`` — a valid two-sided quote: both sides present, positive, and not crossed
   (``bid <= ask``). ``mid = (bid + ask) / 2``.
2. ``last`` — no valid two-sided quote, but a positive last trade.
3. ``close`` — no live price, but a prior close was supplied.
4. ``carry_forward`` — nothing current; carry the last known spot forward.

A crossed or non-positive quote is never silently turned into a mid; it falls
through to the next rung, and the QC layer flags the bad quote separately. When no
rung can produce a price, :class:`NoReferenceSpot` is raised — the snapshot has no
honest spot to report, and that is surfaced, not papered over with a zero.
"""

from __future__ import annotations

from dataclasses import dataclass

# The reference-type labels, in ladder order. A snapshot's reference_type is one
# of these, never blank, so every spot carries the rung that produced it.
REFERENCE_TYPES = ("mid", "last", "close", "carry_forward")


class NoReferenceSpot(Exception):
    """No rung of the ladder could produce a reference spot.

    Carries a plain-language reason so the caller can label why the instrument has
    no honest price (empty/one-sided quote, no last, no close, no prior spot).
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class ReferenceSpot:
    """The chosen reference price and the evidence behind it.

    ``bid``/``ask``/``last`` are the observed quote fields (0.0 when a field was
    absent, so the storage contract's non-negativity holds); ``spread_pct`` is the
    relative spread when a valid mid was used and 0.0 otherwise; ``is_fallback`` is
    True whenever ``reference_type`` is not ``"mid"``.
    """

    value: float
    reference_type: str
    bid: float
    ask: float
    last: float
    spread_pct: float
    is_fallback: bool


def is_valid_two_sided(bid: float | None, ask: float | None) -> bool:
    """Whether a two-sided quote is usable for a mid: present, positive, uncrossed."""
    return bid is not None and ask is not None and bid > 0.0 and ask > 0.0 and bid <= ask


def resolve_reference_spot(
    *,
    bid: float | None,
    ask: float | None,
    last: float | None,
    prior_close: float | None = None,
    prior_spot: float | None = None,
) -> ReferenceSpot:
    """Choose the reference spot by the labeled ladder; raise if no rung applies.

    Look-ahead contract: ``prior_close`` and ``prior_spot`` are the close/carry
    fallback rungs and MUST be values known at or before the snapshot instant. This
    function cannot check that — it sees only the numbers — so the caller owns the
    point-in-time guarantee; feeding a future close here would smuggle look-ahead
    bias in through the back door of an otherwise as-of-clean read.
    """
    observed_bid = bid if (bid is not None and bid > 0.0) else 0.0
    observed_ask = ask if (ask is not None and ask > 0.0) else 0.0
    observed_last = last if (last is not None and last > 0.0) else 0.0

    if is_valid_two_sided(bid, ask):
        assert bid is not None and ask is not None  # narrowed by is_valid_two_sided
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
