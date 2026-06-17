from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from algotrading.core.hashing import canonical_dumps
from algotrading.core.provenance import ProvenanceStamp, SourceRecordRef, validate_stamp

EVENT_BUILT = "built"
EVENT_SIGNOFF_REQUESTED = "signoff_requested"
EVENT_SIGNOFF_RECEIVED = "signoff_received"
EVENT_GATE_EVALUATED = "gate_evaluated"
EVENT_DECISION = "decision"
EVENT_TRANSMIT_ATTEMPT = "transmit_attempt"
_EVENTS = (
    EVENT_BUILT,
    EVENT_SIGNOFF_REQUESTED,
    EVENT_SIGNOFF_RECEIVED,
    EVENT_GATE_EVALUATED,
    EVENT_DECISION,
    EVENT_TRANSMIT_ATTEMPT,
)


class TransmitAuditError(Exception):

    def __init__(self, reason: str, *, field: str, value: object) -> None:
        self.reason = reason
        self.field = field
        self.value = value
        super().__init__(f"{field}={value!r}: {reason}")


@dataclass(frozen=True, slots=True)
class TransmitAudit:

    event_id: str
    binding_hash: str
    event: str
    sequence: int
    detail: str
    event_ts: datetime
    provenance: ProvenanceStamp

    def __post_init__(self) -> None:
        for name in ("event_id", "binding_hash"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise TransmitAuditError("must be a non-empty string", field=name, value=value)
        if self.event not in _EVENTS:
            raise TransmitAuditError(f"must be one of {_EVENTS}", field="event", value=self.event)
        if self.sequence < 0:
            raise TransmitAuditError("must be >= 0", field="sequence", value=self.sequence)
        if self.event_ts.tzinfo is None:
            raise TransmitAuditError(
                "must be timezone-aware", field="event_ts", value=self.event_ts
            )


def _validated(record: TransmitAudit, *, seen: frozenset[str]) -> None:
    if not isinstance(record, TransmitAudit):
        raise TransmitAuditError("must be a TransmitAudit", field="record", value=record)
    validate_stamp(record.provenance)
    if record.event_id in seen:
        raise TransmitAuditError(
            "a record with this id is already in the log (append-only: no overwrite)",
            field="event_id",
            value=record.event_id,
        )


def replay(records: tuple[TransmitAudit, ...]) -> tuple[TransmitAudit, ...]:
    return tuple(sorted(records, key=lambda r: (r.binding_hash, r.sequence)))


@runtime_checkable
class TransmitAuditLog(Protocol):

    def append(self, record: TransmitAudit) -> None: ...

    def read(self, *, binding_hash: str | None = None) -> tuple[TransmitAudit, ...]: ...


def _matches(record: TransmitAudit, *, binding_hash: str | None) -> bool:
    return binding_hash is None or record.binding_hash == binding_hash


class InMemoryTransmitAuditLog:

    def __init__(self) -> None:
        self._records: list[TransmitAudit] = []
        self._ids: set[str] = set()

    def append(self, record: TransmitAudit) -> None:
        _validated(record, seen=frozenset(self._ids))
        self._records.append(record)
        self._ids.add(record.event_id)

    def read(self, *, binding_hash: str | None = None) -> tuple[TransmitAudit, ...]:
        return tuple(r for r in self._records if _matches(r, binding_hash=binding_hash))


class JsonlTransmitAuditLog:

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._records: list[TransmitAudit] = []
        self._ids: set[str] = set()
        if self._path.exists():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = _audit_from_jsonable(json.loads(line))
                self._records.append(record)
                self._ids.add(record.event_id)

    def append(self, record: TransmitAudit) -> None:
        _validated(record, seen=frozenset(self._ids))
        line = canonical_dumps(_audit_to_jsonable(record))
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        self._records.append(record)
        self._ids.add(record.event_id)

    def read(self, *, binding_hash: str | None = None) -> tuple[TransmitAudit, ...]:
        return tuple(r for r in self._records if _matches(r, binding_hash=binding_hash))


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


def _audit_to_jsonable(record: TransmitAudit) -> dict[str, object]:
    return {
        "event_id": record.event_id,
        "binding_hash": record.binding_hash,
        "event": record.event,
        "sequence": record.sequence,
        "detail": record.detail,
        "event_ts": record.event_ts.isoformat(),
        "provenance": _stamp_to_jsonable(record.provenance),
    }


def _audit_from_jsonable(payload: Any) -> TransmitAudit:
    try:
        return TransmitAudit(
            event_id=str(payload["event_id"]),
            binding_hash=str(payload["binding_hash"]),
            event=str(payload["event"]),
            sequence=int(payload["sequence"]),
            detail=str(payload["detail"]),
            event_ts=datetime.fromisoformat(str(payload["event_ts"])),
            provenance=_stamp_from_jsonable(payload["provenance"]),
        )
    except (KeyError, ValueError, TransmitAuditError) as exc:
        raise TransmitAuditError(
            f"malformed audit record on read: {exc}", field="record", value=payload
        ) from exc
