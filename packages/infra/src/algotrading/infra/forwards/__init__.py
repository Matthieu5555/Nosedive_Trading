"""Forward & carry engine — a chain of call/put pairs in, a forward and DF out.

The pure heart of step 6. :func:`estimate_forward` recovers the forward ``F`` and
discount factor ``DF`` jointly from the put-call-parity line, rejects MAD outliers
(via :mod:`algotrading.infra.utils.robust`), and derives the implied carry/dividend,
returning a rich :class:`ForwardEstimate`. :func:`forward_curve_point` projects the
usable part into the stamped ``ForwardCurvePoint`` contract. The parity math lives in
:mod:`forwards.parity`.

    from algotrading.infra.forwards import estimate_forward, ForwardPair, forward_curve_point
"""

from __future__ import annotations

from .estimate import (
    FORWARD_VERSION,
    QUALITY_LABELS,
    REASON_DEGENERATE_FIT,
    REASON_NO_PAIRS,
    REASON_OK,
    REASON_SINGLE_PAIR_FALLBACK,
    REASON_SINGLE_PAIR_NO_DF,
    ForwardError,
    ForwardEstimate,
    ForwardPair,
    StrikePoint,
    estimate_forward,
    forward_curve_point,
)
from .parity import (
    DegenerateParityFit,
    ParityLine,
    parity_forward_from_pair,
    regress_forward_and_discount_factor,
)

__all__ = [
    "FORWARD_VERSION",
    "QUALITY_LABELS",
    "REASON_DEGENERATE_FIT",
    "REASON_NO_PAIRS",
    "REASON_OK",
    "REASON_SINGLE_PAIR_FALLBACK",
    "REASON_SINGLE_PAIR_NO_DF",
    "DegenerateParityFit",
    "ForwardError",
    "ForwardEstimate",
    "ForwardPair",
    "ParityLine",
    "StrikePoint",
    "estimate_forward",
    "forward_curve_point",
    "parity_forward_from_pair",
    "regress_forward_and_discount_factor",
]
