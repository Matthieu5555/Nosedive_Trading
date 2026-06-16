from __future__ import annotations

from dataclasses import dataclass

QUOTE_STATUSES = ("reject", "caution", "usable")
_SEVERITY_RANK = {"reject": 2, "caution": 1, "usable": 0}

Finding = tuple[str, str]


@dataclass(frozen=True, slots=True)
class QuoteAssessment:

    status: str
    reasons: tuple[str, ...]

    @property
    def is_usable(self) -> bool:
        return self.status != "reject"


def check_crossed_or_locked(bid: float | None, ask: float | None) -> Finding | None:
    if bid is None or ask is None:
        return None
    if bid > ask:
        return ("reject", "crossed")
    if bid == ask:
        return ("caution", "locked")
    return None


def check_bid_positive(bid: float | None) -> Finding | None:
    if bid is None or bid <= 0.0:
        return ("caution", "non_positive_bid")
    return None


def check_spread(bid: float | None, ask: float | None, max_spread_pct: float) -> Finding | None:
    if bid is None or ask is None or bid <= 0.0 or ask <= 0.0 or bid > ask:
        return None
    mid = 0.5 * (bid + ask)
    if mid > 0.0 and (ask - bid) / mid > max_spread_pct:
        return ("caution", "wide_spread")
    return None


def check_quote_age(age_seconds: float, max_quote_age_seconds: float) -> Finding | None:
    if age_seconds > max_quote_age_seconds:
        return ("caution", "stale")
    return None


def check_open_interest(open_interest: float, min_open_interest: float) -> Finding | None:
    if open_interest < min_open_interest:
        return ("caution", "low_open_interest")
    return None


def check_price_against_intrinsic(
    price: float, intrinsic: float, max_value: float
) -> Finding | None:
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
    violations: list[int] = []
    for index in range(1, len(strikes)):
        if call_prices[index] > call_prices[index - 1] + 1e-12:
            violations.append(index)
    return tuple(violations)
