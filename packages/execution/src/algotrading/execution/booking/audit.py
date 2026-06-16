from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from algotrading.core.hashing import canonical_dumps
from algotrading.core.provenance import ProvenanceStamp, SourceRecordRef, validate_stamp

COMMIT = "commit"
BLOCK = "block"
_DECISIONS = (COMMIT, BLOCK)


class BookingAuditError(Exception):

    def __init__(self, reason: str, *, field: str, value: object) -> None:
        self.reason = reason
        self.field = field
        self.value = value
        super().__init__(f"{field}={value!r}: {reason}")


@dataclass(frozen=True, slots=True)
class BookingAudit:

    audit_id: str
    booking_id: str
    source_basket_id: str
    trade_date: date
    underlying: str
    decision: str
    fill_ids: tuple[str, ...]
    decision_ts: datetime
    provenance: ProvenanceStamp
    block_reason: str | None = None

    def __post_init__(self) -> None:
        for name in ("audit_id", "booking_id", "source_basket_id", "underlying"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise BookingAuditError("must be a non-empty string", field=name, value=value)
        if self.decision not in _DECISIONS:
            raise BookingAuditError(
                f"must be one of {_DECISIONS}", field="decision", value=self.decision
            )
        if self.decision == BLOCK and not (self.block_reason and self.block_reason.strip()):
            raise BookingAuditError(
                "a block must name its reason", field="block_reason", value=self.block_reason
            )
        if self.decision == COMMIT and self.block_reason is not None:
            raise BookingAuditError(
                "a commit carries no block reason", field="block_reason", value=self.block_reason
            )
        if self.decision == COMMIT and not self.fill_ids:
            raise BookingAuditError(
                "a commit records the fills it wrote", field="fill_ids", value=self.fill_ids
            )
        if self.decision == BLOCK and self.fill_ids:
            raise BookingAuditError(
                "a block writes no fill", field="fill_ids", value=self.fill_ids
            )
        if self.decision_ts.tzinfo is None:
            raise BookingAuditError(
                "must be timezone-aware", field="decision_ts", value=self.decision_ts
            )


def _matches(record: BookingAudit, *, trade_date: date | None, underlying: str | None) -> bool:
    if trade_date is not None and record.trade_date != trade_date:
        return False
    return not (underlying is not None and record.underlying != underlying)


def _validated(record: BookingAudit, *, seen: frozenset[str]) -> None:
    if not isinstance(record, BookingAudit):
        raise BookingAuditError("must be a BookingAudit", field="record", value=record)
    validate_stamp(record.provenance)
    if record.audit_id in seen:
        raise BookingAuditError(
            "a record with this id is already in the log (append-only: no overwrite)",
            field="audit_id",
            value=record.audit_id,
        )


@runtime_checkable
class BookingAuditLog(Protocol):

    def append(self, record: BookingAudit) -> None: ...

    def read(
        self, *, trade_date: date | None = None, underlying: str | None = None
    ) -> tuple[BookingAudit, ...]: ...


class InMemoryBookingAuditLog:

    def __init__(self) -> None:
        self._records: list[BookingAudit] = []
        self._ids: set[str] = set()

    def append(self, record: BookingAudit) -> None:
        _validated(record, seen=frozenset(self._ids))
        self._records.append(record)
        self._ids.add(record.audit_id)

    def read(
        self, *, trade_date: date | None = None, underlying: str | None = None
    ) -> tuple[BookingAudit, ...]:
        return tuple(
            r for r in self._records if _matches(r, trade_date=trade_date, underlying=underlying)
        )


class JsonlBookingAuditLog:

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._records: list[BookingAudit] = []
        self._ids: set[str] = set()
        if self._path.exists():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = _audit_from_jsonable(json.loads(line))
                self._records.append(record)
                self._ids.add(record.audit_id)

    def append(self, record: BookingAudit) -> None:
        _validated(record, seen=frozenset(self._ids))
        line = canonical_dumps(_audit_to_jsonable(record))
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        self._records.append(record)
        self._ids.add(record.audit_id)

    def read(
        self, *, trade_date: date | None = None, underlying: str | None = None
    ) -> tuple[BookingAudit, ...]:
        return tuple(
            r for r in self._records if _matches(r, trade_date=trade_date, underlying=underlying)
        )


def _stamp_to_jsonable(prov: ProvenanceStamp) -> dict[str, object]:
    return {
        "calc_ts": prov.calc_ts.isoformat(),
        "code_version": prov.code_version,
        "config_hashes": dict(prov.config_hashes),
        "source_records": [
            {"table": ref.table, "primary_key": list(ref.primary_key)}
            for ref in prov.source_records
        ],
        "source_timestamps": [ts.isoformat() for ts in prov.source_timestamps],
        "stamp_hash": prov.stamp_hash,
    }


def _stamp_from_jsonable(payload: Any) -> ProvenanceStamp:
    return ProvenanceStamp(
        calc_ts=datetime.fromisoformat(str(payload["calc_ts"])),
        code_version=str(payload["code_version"]),
        config_hashes={str(k): str(v) for k, v in payload["config_hashes"].items()},
        source_records=tuple(
            SourceRecordRef(
                table=str(r["table"]),
                primary_key=tuple(str(k) for k in r["primary_key"]),
            )
            for r in payload["source_records"]
        ),
        source_timestamps=tuple(
            datetime.fromisoformat(str(ts)) for ts in payload["source_timestamps"]
        ),
        stamp_hash=str(payload["stamp_hash"]),
    )


def _audit_to_jsonable(record: BookingAudit) -> dict[str, object]:
    return {
        "audit_id": record.audit_id,
        "booking_id": record.booking_id,
        "source_basket_id": record.source_basket_id,
        "trade_date": record.trade_date.isoformat(),
        "underlying": record.underlying,
        "decision": record.decision,
        "fill_ids": list(record.fill_ids),
        "decision_ts": record.decision_ts.isoformat(),
        "block_reason": record.block_reason,
        "provenance": _stamp_to_jsonable(record.provenance),
    }


def _audit_from_jsonable(payload: Any) -> BookingAudit:
    try:
        return BookingAudit(
            audit_id=str(payload["audit_id"]),
            booking_id=str(payload["booking_id"]),
            source_basket_id=str(payload["source_basket_id"]),
            trade_date=date.fromisoformat(str(payload["trade_date"])),
            underlying=str(payload["underlying"]),
            decision=str(payload["decision"]),
            fill_ids=tuple(str(fid) for fid in payload["fill_ids"]),
            decision_ts=datetime.fromisoformat(str(payload["decision_ts"])),
            provenance=_stamp_from_jsonable(payload["provenance"]),
            block_reason=(
                None if payload["block_reason"] is None else str(payload["block_reason"])
            ),
        )
    except (KeyError, ValueError, BookingAuditError) as exc:
        raise BookingAuditError(
            f"malformed audit record on read: {exc}", field="record", value=payload
        ) from exc
