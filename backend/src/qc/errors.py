"""QC-owned exceptions. Each carries the value that triggered it.

A QC check is the validation plane: when it cannot run, it must say *what* made it
impossible, never raise a bare error. So every exception here holds the offending
object — a mismatched contract key, an empty input — so the failure is as specific
as the checks themselves are required to be.
"""

from __future__ import annotations


class QcError(Exception):
    """Base for every QC failure that is a wiring/precondition bug, not a check fail.

    A check that *fails* returns a ``QcResult`` with ``status="fail"``; that is the
    normal, expected outcome the framework exists to produce. A ``QcError`` is the
    other thing — the check could not even be evaluated because its inputs are
    self-contradictory (a join wired the wrong objects together, an empty batch was
    handed to a check that has no meaningful empty answer).
    """


class ContractKeyMismatchError(QcError):
    """A Greek-sanity reconcile was handed a broker row for a different contract.

    Folds in ADR 0006's deferred precondition: ``risk.reconcile`` compares Greeks
    without asserting ``broker.contract_key == line.contract_key``, so a mis-wired
    join would silently compare the wrong broker Greek to the wrong computed line.
    The Greek-sanity check makes the key match a hard precondition and raises this,
    naming both keys, rather than producing a meaningless discrepancy.
    """

    def __init__(self, line_key: str, broker_key: str) -> None:
        self.line_key = line_key
        self.broker_key = broker_key
        super().__init__(
            f"reconcile key mismatch: line contract_key={line_key!r} "
            f"but broker contract_key={broker_key!r}"
        )


class EmptyBaselineError(QcError):
    """Anomaly detection was asked to judge a value against an empty baseline.

    With no baseline there is no rolling reference, so "is this a spike" has no
    answer. We refuse rather than invent a default that would silently pass every
    value (or fail every value).
    """

    def __init__(self, observed: float) -> None:
        self.observed = observed
        super().__init__(f"cannot detect an anomaly for {observed!r} against an empty baseline")
