"""Named quote-quality checks (roadmap step 7), each a small, separate function.

Quote QC is the gatekeeper between raw quotes and the analytics that trust them.
Rather than one monolithic ``if``, each check is its own named function returning a
``(severity, reason_code)`` when it fires and ``None`` when the quote is fine, so a
quote's verdict reads as the list of named reasons it triggered. ``assess_quote``
runs the local (single-quote) checks and reduces them to one
:class:`QuoteAssessment` with the worst severity and every reason code, so the
verdict is auditable, never a bare boolean.

The cross-sectional checks that need the whole chain — cross-strike monotonicity
here, and the MAD outlier rejection that lives with the forward engine (Eq 24) —
are separate, because a single quote cannot know it is an outlier on its own.
"""

from __future__ import annotations

from dataclasses import dataclass

# Verdicts, worst-first. A quote is usable, usable-with-caution, or rejected.
QUOTE_STATUSES = ("reject", "caution", "usable")
_SEVERITY_RANK = {"reject": 2, "caution": 1, "usable": 0}

# One finding from a single check: its severity and a stable reason code.
Finding = tuple[str, str]


@dataclass(frozen=True, slots=True)
class QuoteAssessment:
    """The verdict on one quote: a status and every reason code that fired."""

    status: str
    reasons: tuple[str, ...]

    @property
    def is_usable(self) -> bool:
        """True when the quote may feed analytics (usable or caution, not reject)."""
        return self.status != "reject"


def check_crossed_or_locked(bid: float | None, ask: float | None) -> Finding | None:
    """A crossed market (bid > ask) is rejected; a locked one (bid == ask) cautions."""
    if bid is None or ask is None:
        return None
    if bid > ask:
        return ("reject", "crossed")
    if bid == ask:
        return ("caution", "locked")
    return None


def check_bid_positive(bid: float | None) -> Finding | None:
    """A missing or non-positive bid cannot anchor a mid; caution."""
    if bid is None or bid <= 0.0:
        return ("caution", "non_positive_bid")
    return None


def check_spread(bid: float | None, ask: float | None, max_spread_pct: float) -> Finding | None:
    """A relative spread wider than the configured maximum cautions."""
    if bid is None or ask is None or bid <= 0.0 or ask <= 0.0 or bid > ask:
        return None  # crossed/one-sided handled by their own checks
    mid = 0.5 * (bid + ask)
    if mid > 0.0 and (ask - bid) / mid > max_spread_pct:
        return ("caution", "wide_spread")
    return None


def check_quote_age(age_seconds: float, max_quote_age_seconds: float) -> Finding | None:
    """A quote older than the staleness threshold cautions (boundary is exclusive)."""
    if age_seconds > max_quote_age_seconds:
        return ("caution", "stale")
    return None


def check_open_interest(open_interest: float, min_open_interest: float) -> Finding | None:
    """Open interest below the minimum cautions (thin contract)."""
    if open_interest < min_open_interest:
        return ("caution", "low_open_interest")
    return None


def check_price_against_intrinsic(
    price: float, intrinsic: float, max_value: float
) -> Finding | None:
    """A price below intrinsic or above its theoretical max is impossible; reject."""
    if price < intrinsic:
        return ("reject", "below_intrinsic")
    if price > max_value:
        return ("reject", "above_max_value")
    return None


def assess_quote(
    *,
    bid: float | None,
    ask: float | None,
    max_spread_pct: float,
    age_seconds: float | None = None,
    max_quote_age_seconds: float | None = None,
    open_interest: float | None = None,
    min_open_interest: float | None = None,
    price: float | None = None,
    intrinsic: float | None = None,
    max_value: float | None = None,
) -> QuoteAssessment:
    """Run every applicable local check and reduce to one verdict.

    A check is applied only when its inputs are supplied (age needs a threshold,
    open interest needs a minimum, the intrinsic check needs price bounds), so a
    caller assesses exactly what it can observe — nothing is assumed.
    """
    findings: list[Finding] = []
    for finding in (
        check_crossed_or_locked(bid, ask),
        check_bid_positive(bid),
        check_spread(bid, ask, max_spread_pct),
    ):
        if finding is not None:
            findings.append(finding)
    if age_seconds is not None and max_quote_age_seconds is not None:
        finding = check_quote_age(age_seconds, max_quote_age_seconds)
        if finding is not None:
            findings.append(finding)
    if open_interest is not None and min_open_interest is not None:
        finding = check_open_interest(open_interest, min_open_interest)
        if finding is not None:
            findings.append(finding)
    if price is not None and intrinsic is not None and max_value is not None:
        finding = check_price_against_intrinsic(price, intrinsic, max_value)
        if finding is not None:
            findings.append(finding)

    if not findings:
        return QuoteAssessment(status="usable", reasons=())
    worst = max((severity for severity, _ in findings), key=_SEVERITY_RANK.__getitem__)
    reasons = tuple(reason for _, reason in findings)
    return QuoteAssessment(status=worst, reasons=reasons)


def cross_strike_monotonicity_violations(
    strikes: tuple[float, ...], call_prices: tuple[float, ...]
) -> tuple[int, ...]:
    """Indices where call prices break monotonicity (must be non-increasing in K).

    A chain-level check: undiscounted call value falls as strike rises, so an index
    whose price exceeds the previous strike's price is a violation. Returns the
    offending indices (empty when the chain is monotone). Strikes must be sorted
    ascending; the check is symmetric for puts by reversing the price relation.
    """
    violations: list[int] = []
    for index in range(1, len(strikes)):
        if call_prices[index] > call_prices[index - 1] + 1e-12:
            violations.append(index)
    return tuple(violations)
