from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ValidationStatus(StrEnum):

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


_STATUS_RANK = {ValidationStatus.PASS: 0, ValidationStatus.WARN: 1, ValidationStatus.FAIL: 2}


def worst_status(statuses: tuple[ValidationStatus, ...]) -> ValidationStatus:
    return max(statuses, key=lambda s: _STATUS_RANK[s], default=ValidationStatus.PASS)


@dataclass(frozen=True, slots=True)
class ValidationCheck:

    check: str
    status: ValidationStatus
    detail: str
    locator: str | None = None
    reason_code: str | None = None
    measured: float | None = None

    def __post_init__(self) -> None:
        if self.status is not ValidationStatus.PASS and self.reason_code is None:
            raise ValueError(f"check {self.check!r} is {self.status} but has no reason_code")


@dataclass(frozen=True, slots=True)
class ValidationReport:

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
        return cls(
            run_id=run_id,
            underlying=underlying,
            as_of=as_of,
            status=worst_status(tuple(c.status for c in checks)),
            checks=checks,
            threshold_version=threshold_version,
        )

    def failures(self) -> tuple[ValidationCheck, ...]:
        return tuple(c for c in self.checks if c.status is not ValidationStatus.PASS)
