"""Run-level validation contracts: per-check outcomes and the run report.

Validation answers a different question from the named QC checks. QC asks "did this
specific object pass its cut-off" (this maturity's fit, this quote's spread); validation
asks "is this whole day's run trustworthy *relative to its own history*". The two planes
are siblings: QC is static and per-object, validation is a rolling-baseline view of the
run. Their failures collapse into one triage shape (see
:mod:`algotrading.infra.validation.triage`).

The specificity discipline carries across: a :class:`ValidationCheck` names the exact
thing that flagged (``locator``) and why (``reason_code``), never a vague banner. A
check that is not ``PASS`` must carry a ``reason_code`` — enforced here, so an
unexplained flag cannot be constructed at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ValidationStatus(StrEnum):
    """The tri-state outcome of one validation check, ordered worst-last."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


_STATUS_RANK = {ValidationStatus.PASS: 0, ValidationStatus.WARN: 1, ValidationStatus.FAIL: 2}


def worst_status(statuses: tuple[ValidationStatus, ...]) -> ValidationStatus:
    """Return the most severe status present (FAIL > WARN > PASS); PASS for an empty set.

    An empty set is a clean PASS — a run with nothing to flag is trustworthy, not
    suspect. The report does not invent a failure from absence.
    """
    return max(statuses, key=lambda s: _STATUS_RANK[s], default=ValidationStatus.PASS)


@dataclass(frozen=True, slots=True)
class ValidationCheck:
    """One named validation outcome.

    ``locator`` points at the specific thing that flagged (e.g. ``"metric=n_iv_points"``)
    and ``reason_code`` is the machine-readable why. ``measured`` carries the number the
    verdict turned on, when there is one.
    """

    check: str
    status: ValidationStatus
    detail: str
    locator: str | None = None
    reason_code: str | None = None
    measured: float | None = None

    def __post_init__(self) -> None:
        # A non-PASS check without a reason is exactly the unexplained red banner this
        # plane exists to prevent, so refuse to build one.
        if self.status is not ValidationStatus.PASS and self.reason_code is None:
            raise ValueError(f"check {self.check!r} is {self.status} but has no reason_code")


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Every validation check for one underlying's daily run, plus the worst status.

    ``run_id`` and ``as_of`` are injected by the caller (never read from a clock), so a
    report reproduces byte-for-byte in replay. ``threshold_version`` brands the report
    with the config version that judged it, the same traceability a ``QcResult`` carries.
    """

    run_id: str
    underlying: str
    as_of: datetime
    status: ValidationStatus
    checks: tuple[ValidationCheck, ...]
    threshold_version: str

    @classmethod
    def from_checks(
        cls,
        *,
        run_id: str,
        underlying: str,
        as_of: datetime,
        checks: tuple[ValidationCheck, ...],
        threshold_version: str,
    ) -> ValidationReport:
        """Build a report from its checks, with overall status the worst single check."""
        return cls(
            run_id=run_id,
            underlying=underlying,
            as_of=as_of,
            status=worst_status(tuple(c.status for c in checks)),
            checks=checks,
            threshold_version=threshold_version,
        )

    def failures(self) -> tuple[ValidationCheck, ...]:
        """The checks that did not pass — the triage list, each located and explained."""
        return tuple(c for c in self.checks if c.status is not ValidationStatus.PASS)
