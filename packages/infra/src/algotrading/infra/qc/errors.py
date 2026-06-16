from __future__ import annotations


class QcError(Exception):
    pass


class ContractKeyMismatchError(QcError):

    def __init__(self, line_key: str, broker_key: str) -> None:
        self.line_key = line_key
        self.broker_key = broker_key
        super().__init__(
            f"reconcile key mismatch: line contract_key={line_key!r} "
            f"but broker contract_key={broker_key!r}"
        )


class EmptyBaselineError(QcError):

    def __init__(self, observed: float) -> None:
        self.observed = observed
        super().__init__(f"cannot detect an anomaly for {observed!r} against an empty baseline")
